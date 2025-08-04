#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
authenticated_recording.py - 認証付き録画エンジン（Phase 1完全修正版）
TwitCasting年齢制限・限定配信対応の完全自動録画システム
"""

import asyncio
import logging
import json
import time
import uuid
import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field
import subprocess
import re

# 外部モジュールをインポート
from recording_options import RecordingOptions

# auth_core.pyとの統合
try:
    from auth_core import TwitCastingAuth, LimitedStreamAuth
    AUTH_CORE_AVAILABLE = True
except ImportError:
    AUTH_CORE_AVAILABLE = False

# Playwright依存関係
try:
    from playwright.async_api import async_playwright, BrowserContext, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

class SessionStatus:
    INITIALIZING = "initializing"
    BROWSER_STARTING = "browser_starting"
    AUTHENTICATING = "authenticating"
    WAITING_FOR_STREAM = "waiting_for_stream"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    ERROR = "error"

@dataclass
class RecordingSession:
    """録画セッション情報"""
    session_id: str
    url: str
    username: str
    browser_context: Optional[BrowserContext] = None
    process: Optional[asyncio.subprocess.Process] = None
    start_time: Optional[datetime] = None
    output_file: Optional[Path] = None
    status: str = SessionStatus.INITIALIZING
    retry_count: int = 0
    last_error: Optional[str] = None
    m3u8_url: Optional[str] = None
    cookie_header: Optional[str] = None

class AuthenticatedRecordingEngine:
    """認証付き録画エンジン（Phase 1完全修正版）"""
    
    AGE_VERIFY_TIMEOUT = 3000
    PASSWORD_INPUT_TIMEOUT = 5000
    
    def __init__(self, config_manager, system_config):
        self.config_manager = config_manager
        self.system_config = system_config
        self.logger = logging.getLogger(__name__)
        
        self.recordings_dir = self.system_config.recordings_dir / "videos"
        self.temp_dir = self.system_config.recordings_dir / "temp"
        self.cookies_dir = self.system_config.data_dir / "cookies"
        self.browser_sessions_dir = self.system_config.data_dir / "browser_sessions"
        
        for directory in [self.recordings_dir, self.temp_dir, self.cookies_dir, self.browser_sessions_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        
        self.active_sessions: Dict[str, RecordingSession] = {}
        self.playwright_instance = None
        
        # ✅ 修正: auth_core.pyとの統合
        self.auth_manager = None
        self.limited_auth = None
        if AUTH_CORE_AVAILABLE:
            try:
                self.auth_manager = TwitCastingAuth(str(self.system_config.data_dir))
                self.limited_auth = LimitedStreamAuth(self.auth_manager)
                self.logger.info("🔐 auth_core統合完了")
            except Exception as e:
                self.logger.warning(f"auth_core統合失敗: {e}")
        
        self.logger.info("認証付き録画エンジン初期化完了（Phase 1修正版）")
    
    def _extract_username(self, url: str) -> str:
        """✅ 修正: URLからユーザー名抽出"""
        try:
            # 通常の配信URL
            match = re.search(r'twitcasting\.tv/([^/\?]+)', url)
            if match:
                return match.group(1)
            
            # フォールバック
            return url.split('/')[-1].split('?')[0] if '/' in url else "unknown"
        except Exception:
            return "unknown"

    def _generate_session_id(self, url: str) -> str:
        """セッションID生成"""
        username = self._extract_username(url)
        timestamp = int(time.time())
        unique_id = str(uuid.uuid4())[:8]
        return f"session_{timestamp}_{unique_id}"
    
    async def start_authenticated_recording(self, url: str, options: RecordingOptions) -> bool:
        """認証付き録画開始"""
        try:
            username = self._extract_username(url)
            session_id = options.session_name or self._generate_session_id(url)
            
            self.logger.info(f"🔐 認証付き録画開始: {username} (セッション: {session_id})")
            
            if not options.confirmed_by_user:
                self.logger.error(f"❌ ユーザー確認が必要: {username}")
                return False
            
            session = RecordingSession(
                session_id=session_id, url=url, username=username,
                start_time=datetime.now(), status=SessionStatus.INITIALIZING
            )
            self.active_sessions[session_id] = session
            
            asyncio.create_task(self._execute_recording_session_with_retry(session, options))
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 認証付き録画開始エラー: {url} - {e}", exc_info=True)
            return False
    
    async def _execute_recording_session_with_retry(self, session: RecordingSession, options: RecordingOptions):
        """録画セッション実行（再試行対応）"""
        max_retries = options.max_retries
        while session.retry_count < max_retries:
            try:
                session.retry_count += 1
                self.logger.info(f"🔄 録画試行 {session.retry_count}/{max_retries}: {session.username}")
                
                if await self._execute_recording_session(session, options):
                    return # 成功したら終了
                
                if session.retry_count < max_retries:
                    delay = options.retry_base_delay * (2 ** (session.retry_count - 1))
                    self.logger.info(f"🕐 {delay}秒後に再試行: {session.username}")
                    await asyncio.sleep(delay)
            
            except Exception as e:
                session.last_error = str(e)
                self.logger.error(f"❌ 録画試行エラー: {session.username} - {e}", exc_info=True)
                if session.retry_count < max_retries:
                    delay = options.retry_base_delay * (2 ** session.retry_count)
                    await asyncio.sleep(delay)
        
        session.status = SessionStatus.FAILED
        self.logger.error(f"❌ 録画完全失敗: {session.username} (最大試行回数到達)")
        await self._cleanup_session(session)

    async def _execute_recording_session(self, session: RecordingSession, options: RecordingOptions) -> bool:
        """録画セッション実行（auth_core統合版）"""
        try:
            session.status = SessionStatus.AUTHENTICATING
            
            # ✅ 修正: auth_core.pyを使用した認証
            if self.limited_auth and AUTH_CORE_AVAILABLE:
                self.logger.info(f"🔐 auth_core認証開始: {session.username}")
                
                # パスワード設定
                if options.password:
                    self.limited_auth.set_stream_password(session.url, options.password)
                
                # 配信認証実行
                auth_result = await self.limited_auth.authenticate_for_stream(
                    session.url, headless=options.headless
                )
                
                if auth_result["success"]:
                    session.m3u8_url = auth_result["m3u8_url"]
                    session.cookie_header = auth_result["cookie_header"]
                    session.status = SessionStatus.RECORDING
                    
                    # yt-dlpで録画開始
                    return await self._start_ytdlp_recording_with_auth(session, auth_result, options)
                else:
                    self.logger.error(f"❌ 認証失敗: {session.username} - {auth_result['error']}")
                    session.status = SessionStatus.FAILED
                    return False
            
            else:
                # フォールバック: 基本的なPlaywright処理
                self.logger.warning(f"⚠️ auth_core未利用、フォールバック処理: {session.username}")
                return await self._execute_fallback_recording(session, options)
            
        except Exception as e:
            session.last_error = str(e)
            self.logger.error(f"❌ 録画セッション実行エラー: {session.username} - {e}", exc_info=True)
            return False
            
        finally:
            if session.status != SessionStatus.RECORDING:
                await self._cleanup_session(session)
    
    async def _start_ytdlp_recording_with_auth(self, session: RecordingSession, 
                                             auth_result: Dict[str, Any], 
                                             options: RecordingOptions) -> bool:
        """✅ 修正: auth_core結果を使用したyt-dlp録画"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = self.temp_dir / f"{session.username}_{timestamp}.mp4"
            session.output_file = output_file
            
            # Cookie一時ファイル作成
            cookie_file = None
            if session.cookie_header:
                cookie_file = self.temp_dir / f"cookies_{session.session_id}.txt"
                await self._create_netscape_cookie_file(cookie_file, session.cookie_header, session.url)
            
            # yt-dlpコマンド構築
            cmd = [
                'yt-dlp',
                session.m3u8_url or session.url,
                '--output', str(output_file),
                '--no-live-from-start',
                '--format', options.quality,
                '--no-part',
                '--no-mtime'
            ]
            
            if cookie_file and cookie_file.exists():
                cmd.extend(['--cookies', str(cookie_file)])
            
            self.logger.info(f"🎬 yt-dlp録画開始: {session.username}")
            self.logger.debug(f"yt-dlpコマンド: {' '.join(cmd)}")
            
            # プロセス作成フラグ（Windows対応）
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            session.process = await asyncio.create_subprocess_exec(
                *cmd, 
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE, 
                creationflags=creation_flags
            )
            
            # プロセス監視開始
            asyncio.create_task(self._monitor_recording_process(session, cookie_file))
            return True
            
        except Exception as e:
            self.logger.error(f"yt-dlp録画開始エラー: {e}")
            return False
    
    async def _create_netscape_cookie_file(self, cookie_file: Path, cookie_header: str, url: str):
        """✅ 修正: Netscape形式Cookieファイル作成"""
        try:
            # ドメイン抽出
            import urllib.parse
            parsed_url = urllib.parse.urlparse(url)
            domain = parsed_url.netloc
            
            # Netscape形式でCookieファイル作成
            with open(cookie_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# This is a generated file! Do not edit.\n\n")
                
                for cookie_pair in cookie_header.split('; '):
                    if '=' in cookie_pair:
                        name, value = cookie_pair.split('=', 1)
                        # Netscape形式の行
                        f.write(f"{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
            
            self.logger.debug(f"Netscape Cookieファイル作成: {cookie_file}")
            
        except Exception as e:
            self.logger.error(f"Cookieファイル作成エラー: {e}")
    
    async def _execute_fallback_recording(self, session: RecordingSession, options: RecordingOptions) -> bool:
        """✅ 修正: フォールバック録画処理"""
        if not PLAYWRIGHT_AVAILABLE:
            self.logger.error("PlaywrightもAuth coreも利用できません")
            return False
        
        page = None
        try:
            session.status = SessionStatus.BROWSER_STARTING
            await self._ensure_browser_session(session, options)
            
            session.status = SessionStatus.AUTHENTICATING
            page = await session.browser_context.new_page()
            
            await page.goto(session.url, timeout=30000)
            await self._handle_stream_barriers(page, options.password)
            
            session.status = SessionStatus.WAITING_FOR_STREAM
            m3u8_url = await self._wait_for_stream_start(page, session.username, options.timeout_minutes)
            
            if not m3u8_url:
                session.status = SessionStatus.FAILED
                return False
            
            session.status = SessionStatus.RECORDING
            cookie_file = await self._export_fresh_cookies(session, page)
            return await self._start_ytdlp_recording(session, cookie_file, options)
            
        except Exception as e:
            session.last_error = str(e)
            self.logger.error(f"❌ フォールバック録画エラー: {session.username} - {e}", exc_info=True)
            return False
            
        finally:
            if page:
                await page.close()
    
    async def _ensure_browser_session(self, session: RecordingSession, options: RecordingOptions):
        """ブラウザセッション確保"""
        try:
            if self.playwright_instance is None:
                self.playwright_instance = await async_playwright().start()
            
            session_dir = self.browser_sessions_dir / session.session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            
            session.browser_context = await self.playwright_instance.chromium.launch_persistent_context(
                user_data_dir=str(session_dir),
                headless=options.headless,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            self.logger.info(f"✅ ブラウザセッション作成: {session.session_id}")
        except Exception as e:
            self.logger.error(f"❌ ブラウザセッション作成エラー: {e}", exc_info=True)
            raise

    async def _handle_stream_barriers(self, page, password: Optional[str]):
        """配信障壁処理"""
        try:
            await self._handle_age_verification(page)
            if password:
                await self._handle_password_input(page, password)
        except Exception as e:
            self.logger.error(f"障壁処理エラー: {e}")

    async def _handle_age_verification(self, page):
        """年齢制限確認処理"""
        try:
            # 年齢確認ボタンを探す
            age_button = page.locator("button:visible", has_text="はい")
            if await age_button.count() > 0:
                self.logger.info("🔞 年齢確認画面を突破")
                await age_button.first.click()
                await page.wait_for_load_state('domcontentloaded', timeout=self.AGE_VERIFY_TIMEOUT)
        except Exception as e:
            self.logger.warning(f"年齢確認処理エラー: {e}")

    async def _handle_password_input(self, page, password: str):
        """パスワード入力処理"""
        try:
            self.logger.info("🔑 限定配信パスワード入力処理開始")
            
            password_input = page.locator("input[name='password']")
            if await password_input.count() > 0:
                await password_input.fill(password)
                
                submit_button = page.locator("button[type='submit']")
                if await submit_button.count() > 0:
                    await submit_button.first.click()
                    await page.wait_for_load_state('domcontentloaded', timeout=self.PASSWORD_INPUT_TIMEOUT)
                    self.logger.info("✅ パスワード入力完了")
        except Exception as e:
            self.logger.error(f"パスワード入力エラー: {e}")

    async def _wait_for_stream_start(self, page, username: str, timeout_minutes: int) -> Optional[str]:
        """✅ 修正: 配信開始待機"""
        self.logger.info(f"📡 配信開始待機中: {username}")
        try:
            async with page.expect_response(
                lambda res: '.m3u8' in res.url, 
                timeout=timeout_minutes * 60000
            ) as res_info:
                response = await res_info.value
                self.logger.info(f"🎯 m3u8 URL検出: {username}")
                return response.url
        except Exception:
            self.logger.error(f"❌ 配信検出失敗: {username}")
            return None

    async def _export_fresh_cookies(self, session: RecordingSession, page) -> Path:
        """新鮮なCookie取得"""
        try:
            cookie_file = self.cookies_dir / f"cookies_{session.session_id}.txt"
            
            # ページからCookie取得
            cookies = await session.browser_context.cookies()
            
            # Netscape形式で保存
            with open(cookie_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# This is a generated file! Do not edit.\n\n")
                
                for cookie in cookies:
                    f.write(f"{cookie['domain']}\tTRUE\t{cookie['path']}\t{cookie['secure']}\t0\t{cookie['name']}\t{cookie['value']}\n")
            
            self.logger.info(f"🍪 Cookie出力完了: {cookie_file}")
            return cookie_file
            
        except Exception as e:
            self.logger.error(f"Cookie取得エラー: {e}")
            return None

    async def _start_ytdlp_recording(self, session: RecordingSession, cookie_file: Path, options: RecordingOptions) -> bool:
        """✅ 修正: yt-dlp録画開始"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = self.temp_dir / f"{session.username}_{timestamp}.mp4"
            session.output_file = output_file
            
            cmd = ['yt-dlp', session.url, '--output', str(output_file), '--no-live-from-start',
                   '--format', options.quality, '--no-part']
            if cookie_file and cookie_file.exists():
                cmd.extend(['--cookies', str(cookie_file)])
            
            self.logger.info(f"🎬 yt-dlp録画開始: {session.username}")
            
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            session.process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=creation_flags
            )
            
            asyncio.create_task(self._monitor_recording_process(session, cookie_file))
            return True
        except Exception as e:
            self.logger.error(f"yt-dlp録画開始エラー: {e}")
            return False

    async def _monitor_recording_process(self, session: RecordingSession, cookie_file: Optional[Path] = None):
        """録画プロセス監視"""
        try:
            if not session.process: 
                return
            
            stdout, stderr = await session.process.communicate()
            
            if session.process.returncode == 0:
                session.status = SessionStatus.COMPLETED
                self.logger.info(f"✅ 録画完了: {session.username}")
                
                # 一時ファイルから最終ディレクトリに移動
                if session.output_file and session.output_file.exists():
                    final_file = self.recordings_dir / session.output_file.name
                    session.output_file.rename(final_file)
                    session.output_file = final_file
                    self.logger.info(f"📁 ファイル移動完了: {final_file}")
            else:
                session.status = SessionStatus.FAILED
                error_msg = stderr.decode('utf-8', errors='ignore')
                self.logger.error(f"❌ 録画失敗: {session.username} (code: {session.process.returncode})")
                self.logger.debug(f"エラー詳細: {error_msg}")
        
        except Exception as e:
            session.status = SessionStatus.ERROR
            self.logger.error(f"プロセス監視エラー: {session.username} - {e}")
        
        finally:
            # クリーンアップ
            if cookie_file and cookie_file.exists():
                try:
                    cookie_file.unlink()
                except:
                    pass
            await self._cleanup_session(session)

    async def _cleanup_session(self, session: RecordingSession):
        """✅ 修正: セッションクリーンアップ"""
        try:
            if session.browser_context:
                await session.browser_context.close()
            
            session_dir = self.browser_sessions_dir / session.session_id
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
            
            if session.session_id in self.active_sessions:
                del self.active_sessions[session.session_id]
            
            self.logger.info(f"🧹 セッションクリーンアップ完了: {session.session_id}")
        except Exception as e:
            self.logger.error(f"セッションクリーンアップエラー: {e}")

    async def stop_recording(self, url: str) -> bool:
        """録画停止"""
        try:
            target_session = None
            for session in self.active_sessions.values():
                if session.url == url:
                    target_session = session
                    break
            
            if not target_session:
                self.logger.warning(f"停止対象が見つかりません: {url}")
                return False
            
            target_session.status = SessionStatus.STOPPED
            
            if target_session.process:
                target_session.process.terminate()
                try:
                    await asyncio.wait_for(target_session.process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    target_session.process.kill()
                    await target_session.process.wait()
            
            await self._cleanup_session(target_session)
            self.logger.info(f"✅ 録画停止完了: {target_session.username}")
            return True
            
        except Exception as e:
            self.logger.error(f"録画停止エラー: {e}")
            return False

    def get_active_recordings(self) -> Dict[str, Any]:
        """アクティブな録画一覧取得"""
        return {
            session_id: {
                'url': session.url,
                'username': session.username,
                'status': session.status,
                'start_time': session.start_time.isoformat() if session.start_time else None,
                'duration': str(datetime.now() - session.start_time).split('.')[0] if session.start_time else "0:00:00"
            }
            for session_id, session in self.active_sessions.items()
        }

    async def shutdown(self):
        """シャットダウン"""
        self.logger.info("認証付き録画エンジンシャットダウン")
        
        # 全録画停止
        sessions_to_stop = list(self.active_sessions.values())
        for session in sessions_to_stop:
            await self.stop_recording(session.url)
        
        # Playwright終了
        if self.playwright_instance:
            await self.playwright_instance.stop()
            self.playwright_instance = None