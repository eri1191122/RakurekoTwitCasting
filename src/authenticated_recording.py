#!/usr/bin/env python3
"""
authenticated_recording.py - 認証付き録画エンジン
TwitCasting年齢制限・限定配信対応の完全自動録画システム

添付ファイルのコンセプトに基づく実装：
1. ブラウザでログイン状態を確保
2. m3u8 URL検出の瞬間にCookie取得
3. yt-dlpに新鮮なCookieを渡して録画開始
"""

import asyncio
import logging
import time
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
from playwright.async_api import async_playwright, Browser, Page
import platform

class AuthenticatedRecordingEngine:
    """認証付き録画エンジン"""
    
    def __init__(self, config_manager, system_config):
        self.config_manager = config_manager
        self.system_config = system_config
        self.logger = logging.getLogger(__name__)
        
        # ディレクトリ設定
        self.recordings_dir = self.system_config.recordings_dir / "videos"
        self.cookies_dir = self.system_config.data_dir / "cookies"
        self.user_data_dir = self.system_config.data_dir / "browser_profile"
        
        # ディレクトリ作成
        for directory in [self.recordings_dir, self.cookies_dir, self.user_data_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        
        # 録画管理
        self.active_recordings: Dict[str, Dict[str, Any]] = {}
        self.browser: Optional[Browser] = None
        
        self.logger.info("認証付き録画エンジン初期化完了")
    
    async def start_recording(self, url: str, password: Optional[str] = None) -> bool:
        """通常録画開始（監視システム互換）"""
        return await self.start_authenticated_recording(url, password)
    
    async def start_authenticated_recording(self, url: str, password: Optional[str] = None) -> bool:
        """認証付き録画開始"""
        try:
            username = self._extract_username(url)
            self.logger.info(f"🔐 認証付き録画開始: {username}")
            
            # 1. ブラウザ起動（ログイン状態維持）
            await self._ensure_browser()
            
            # 2. 配信ページにアクセス
            page = await self.browser.new_page()
            await page.goto(url)
            
            # 3. 年齢制限確認ページの処理
            await self._handle_age_verification(page)
            
            # 4. 限定配信パスワード入力
            if password:
                await self._handle_password_input(page, password)
            
            # 5. 配信開始待機＆m3u8検出
            m3u8_url = await self._wait_for_stream_start(page, username)
            
            if not m3u8_url:
                self.logger.error(f"❌ 配信検出失敗: {username}")
                await page.close()
                return False
            
            # 6. ジャストインタイム・Cookie取得
            cookie_file = await self._export_fresh_cookies(page, username)
            
            # 7. yt-dlp録画開始
            success = await self._start_ytdlp_recording(url, username, cookie_file)
            
            await page.close()
            return success
            
        except Exception as e:
            self.logger.error(f"❌ 認証付き録画エラー: {url} - {e}")
            return False
    
    async def _ensure_browser(self):
        """ブラウザ確保（ログイン状態維持）"""
        if self.browser is None:
            playwright = await async_playwright().start()
            
            # Chrome起動（user_dataでログイン状態維持）
            self.browser = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=False,  # 初回はheadless=Falseでログイン確認
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-first-run',
                    '--disable-dev-shm-usage'
                ]
            )
            
            self.logger.info("✅ ブラウザ起動完了（ログイン状態維持）")
    
    async def _handle_age_verification(self, page: Page):
        """年齢制限確認ページの処理"""
        try:
            # 年齢制限確認ボタンを探す
            age_verify_selectors = [
                'input[value="はい"]',
                'button:has-text("はい")',
                '.age-verify-yes',
                '[data-testid="age-verify-yes"]'
            ]
            
            for selector in age_verify_selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=3000)
                    if element:
                        await element.click()
                        self.logger.info("✅ 年齢制限確認ページ突破")
                        await page.wait_for_timeout(2000)
                        return
                except:
                    continue
            
            # ページタイトルから年齢制限ページか判定
            title = await page.title()
            if '年齢' in title or 'age' in title.lower():
                self.logger.warning("⚠️ 年齢制限ページ検出（自動処理失敗）")
        
        except Exception as e:
            self.logger.debug(f"年齢制限処理: {e}")
    
    async def _handle_password_input(self, page: Page, password: str):
        """限定配信パスワード入力"""
        try:
            self.logger.info(f"🔑 限定配信パスワード入力試行: {password}")
            
            # 少し待機してページ読み込み完了
            await page.wait_for_timeout(3000)
            
            # より広範囲のパスワード入力フィールドを探す
            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[placeholder*="password"]',
                'input[placeholder*="パスワード"]',
                'input[placeholder*="合言葉"]',
                '.password-input',
                '#password',
                'input.form-control[type="text"]',  # テキストタイプの場合もある
                'input[autocomplete="current-password"]'
            ]
            
            password_found = False
            
            for selector in password_selectors:
                try:
                    # より長い待機時間
                    element = await page.wait_for_selector(selector, timeout=5000)
                    if element:
                        # 要素の可視性確認
                        is_visible = await element.is_visible()
                        if is_visible:
                            await element.fill(password)
                            password_found = True
                            self.logger.info(f"✅ パスワード入力成功: {selector}")
                            
                            # 送信ボタンを探してクリック
                            submit_selectors = [
                                'button[type="submit"]',
                                'input[type="submit"]',
                                'button:has-text("視聴する")',
                                'button:has-text("送信")',
                                'button:has-text("確認")',
                                'button:has-text("OK")',
                                '.submit-button',
                                '.btn-primary'
                            ]
                            
                            submit_clicked = False
                            for submit_selector in submit_selectors:
                                try:
                                    submit_btn = await page.wait_for_selector(submit_selector, timeout=2000)
                                    if submit_btn:
                                        is_submit_visible = await submit_btn.is_visible()
                                        if is_submit_visible:
                                            await submit_btn.click()
                                            submit_clicked = True
                                            self.logger.info(f"✅ 送信ボタンクリック: {submit_selector}")
                                            break
                                except:
                                    continue
                            
                            if not submit_clicked:
                                # Enterキーで送信を試行
                                await element.press('Enter')
                                self.logger.info("✅ Enterキーで送信")
                            
                            await page.wait_for_timeout(3000)
                            return
                except:
                    continue
            
            if not password_found:
                # ページ内容をデバッグ出力
                page_content = await page.content()
                if 'password' in page_content.lower() or 'パスワード' in page_content or '合言葉' in page_content:
                    self.logger.warning("⚠️ パスワード関連要素は存在するが、入力フィールドが見つかりません")
                    # 手動入力用の待機時間
                    self.logger.info("🔧 手動でパスワードを入力してください（30秒待機）")
                    await page.wait_for_timeout(30000)
                else:
                    self.logger.info("ℹ️ 限定配信ではない可能性があります")
            
        except Exception as e:
            self.logger.error(f"パスワード入力エラー: {e}")
    
    async def _wait_for_stream_start(self, page: Page, username: str, timeout: int = 300) -> Optional[str]:
        """配信開始待機＆m3u8検出"""
        self.logger.info(f"📡 配信開始待機中: {username}")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # ネットワークリクエストを監視してm3u8 URLを検出
                async with page.expect_response(lambda response: '.m3u8' in response.url) as response_info:
                    # 1秒待機
                    await page.wait_for_timeout(1000)
                    
                    # ページを軽くリロードして配信状態をチェック
                    await page.reload()
                
                response = await response_info.value
                m3u8_url = response.url
                
                self.logger.info(f"🎯 m3u8 URL検出: {username}")
                self.logger.debug(f"m3u8 URL: {m3u8_url}")
                return m3u8_url
                
            except Exception:
                # 配信開始待機（5秒間隔）
                await page.wait_for_timeout(5000)
                
                # 配信状態をチェック
                try:
                    # ライブ配信インジケーターを探す
                    live_indicators = [
                        '.tw-player-status-live',
                        '.live-indicator',
                        '[data-testid="live-indicator"]',
                        '.streaming-status'
                    ]
                    
                    for indicator in live_indicators:
                        element = await page.query_selector(indicator)
                        if element:
                            is_visible = await element.is_visible()
                            if is_visible:
                                self.logger.info(f"📡 配信中を検出: {username}")
                                # 配信中の場合、m3u8検出を継続
                                break
                
                except Exception:
                    pass
        
        self.logger.warning(f"⚠️ 配信開始タイムアウト: {username}")
        return None
    
    async def _export_fresh_cookies(self, page: Page, username: str) -> Path:
        """ジャストインタイム・Cookie取得"""
        try:
            # 最新のCookieを取得
            cookies = await page.context.cookies()
            
            # Netscape形式でCookie出力
            cookie_file = self.cookies_dir / f"{username}_cookies.txt"
            
            with open(cookie_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# This file contains the cookies for TwitCasting authentication\n")
                
                for cookie in cookies:
                    # Netscape形式: domain, domain_specified, path, secure, expires, name, value
                    domain = cookie.get('domain', '')
                    domain_specified = 'TRUE' if domain.startswith('.') else 'FALSE'
                    path = cookie.get('path', '/')
                    secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
                    expires = str(int(cookie.get('expires', 0)))
                    name = cookie.get('name', '')
                    value = cookie.get('value', '')
                    
                    # TwitCasting関連のCookieのみ出力
                    if 'twitcasting' in domain.lower() or 'tw' in domain:
                        line = f"{domain}\t{domain_specified}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n"
                        f.write(line)
            
            self.logger.info(f"🍪 新鮮なCookie出力完了: {username}")
            return cookie_file
            
        except Exception as e:
            self.logger.error(f"Cookie出力エラー: {e}")
            return None
    
    async def _start_ytdlp_recording(self, url: str, username: str, cookie_file: Path) -> bool:
        """yt-dlp録画開始"""
        try:
            # 出力ファイル名生成
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = self.recordings_dir / f"{username}_{timestamp}.mp4"
            
            # yt-dlpコマンド構築（配信中用最適化）
            cmd = [
                'yt-dlp',
                url,
                '--output', str(output_file),
                '--cookies', str(cookie_file),
                '--no-live-from-start',  # 現在時刻から録画開始
                '--format', 'b',  # 警告を回避
                '--no-part'
            ]
            
            self.logger.info(f"🎬 yt-dlp録画開始: {username}")
            self.logger.debug(f"コマンド: {' '.join(cmd)}")
            
            # プロセス作成フラグ（Windows対応）
            creation_flags = 0
            if platform.system() == "Windows":
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            # 非同期プロセス開始
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags
            )
            
            # 録画情報を保存
            recording_info = {
                'url': url,
                'username': username,
                'process': process,
                'output_file': str(output_file),
                'start_time': datetime.now().isoformat(),
                'cookie_file': str(cookie_file)
            }
            
            self.active_recordings[url] = recording_info
            
            # プロセス監視タスクを開始
            asyncio.create_task(self._monitor_ytdlp_process(url, recording_info))
            
            self.logger.info(f"✅ 録画開始成功: {username}")
            return True
            
        except Exception as e:
            self.logger.error(f"yt-dlp録画開始エラー: {e}")
            return False
    
    async def _monitor_ytdlp_process(self, url: str, recording_info: Dict[str, Any]):
        """yt-dlpプロセス監視"""
        username = recording_info['username']
        process = recording_info['process']
        
        try:
            # プロセス完了待機
            stdout, stderr = await process.communicate()
            
            # 結果確認
            if process.returncode == 0:
                output_file = Path(recording_info['output_file'])
                if output_file.exists():
                    file_size = output_file.stat().st_size
                    self.logger.info(f"✅ 録画完了: {username} ({self._format_file_size(file_size)})")
                else:
                    self.logger.warning(f"⚠️ 録画完了（ファイル未確認）: {username}")
            else:
                self.logger.error(f"❌ 録画失敗: {username} (終了コード: {process.returncode})")
                if stderr:
                    error_msg = stderr.decode('utf-8', errors='ignore')
                    self.logger.error(f"エラー詳細: {error_msg}")
            
            # アクティブリストから削除
            if url in self.active_recordings:
                del self.active_recordings[url]
                
        except Exception as e:
            self.logger.error(f"プロセス監視エラー: {username} - {e}")
    
    def _extract_username(self, url: str) -> str:
        """URLからユーザー名抽出"""
        try:
            return url.rstrip('/').split('/')[-1]
        except:
            return "unknown"
    
    def _format_file_size(self, size_bytes: int) -> str:
        """ファイルサイズフォーマット"""
        if size_bytes == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        
        return f"{size_bytes:.1f} TB"
    
    def is_recording(self, url: str) -> bool:
        """録画中確認"""
        return url in self.active_recordings
    
    def get_active_recordings(self) -> Dict[str, Any]:
        """アクティブな録画一覧取得"""
        return {url: info.copy() for url, info in self.active_recordings.items()}
    
    async def stop_recording(self, url: str) -> bool:
        """録画停止"""
        if url not in self.active_recordings:
            return False
        
        try:
            recording_info = self.active_recordings[url]
            process = recording_info['process']
            username = recording_info['username']
            
            # プロセス終了
            process.terminate()
            await asyncio.sleep(3)
            
            if process.returncode is None:
                process.kill()
                await process.wait()
            
            self.logger.info(f"✅ 録画停止: {username}")
            return True
            
        except Exception as e:
            self.logger.error(f"録画停止エラー: {e}")
            return False
    
    async def shutdown(self):
        """シャットダウン"""
        self.logger.info("認証付き録画エンジンシャットダウン")
        
        # 全録画停止
        for url in list(self.active_recordings.keys()):
            await self.stop_recording(url)
        
        # ブラウザ終了
        if self.browser:
            await self.browser.close()
            self.browser = None

# ==================================================
# 単体テスト・使用例
# ==================================================

async def test_authenticated_recording():
    """認証付き録画テスト"""
    print("🧪 認証付き録画システムテスト")
    
    # 設定準備（ダミー）
    class DummyConfig:
        recordings_dir = Path.cwd() / "recordings"
        data_dir = Path.cwd() / "data"
    
    class DummyConfigManager:
        pass
    
    # エンジン初期化
    engine = AuthenticatedRecordingEngine(DummyConfigManager(), DummyConfig())
    
    try:
        # テスト録画開始
        test_url = "https://twitcasting.tv/c:kutuna_"
        success = await engine.start_authenticated_recording(test_url)
        
        if success:
            print("✅ 録画開始成功")
            
            # 30秒待機
            await asyncio.sleep(30)
            
            # 録画停止
            await engine.stop_recording(test_url)
        else:
            print("❌ 録画開始失敗")
    
    except Exception as e:
        print(f"❌ テストエラー: {e}")
    
    finally:
        await engine.shutdown()

if __name__ == "__main__":
    asyncio.run(test_authenticated_recording())