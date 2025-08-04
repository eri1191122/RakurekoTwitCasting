#!#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
group_stream_recorder.py - 成功例完全踏襲によるグループ配信録画エンジン
twitcasting_auto_record.py の設計思想を100%移植・グループ配信特化
"""

import asyncio
import logging
import os
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# 成功例と同じ依存関係のみ使用
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

import subprocess
import threading

logger = logging.getLogger(__name__)

class GroupStreamRecorder:
    """
    成功例完全踏襲・グループ配信特化録画エンジン
    twitcasting_auto_record.py の設計思想を100%移植
    """
    
    def __init__(self, base_dir: str = None):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.recordings_dir = self.base_dir / "recordings"
        self.temp_dir = self.base_dir / "recordings" / "temp"
        self.cookies_dir = self.base_dir / "data" / "cookies"
        self.browser_data_dir = self.base_dir / "data" / "browser_profile"
        
        # ディレクトリ作成
        for directory in [self.recordings_dir, self.temp_dir, self.cookies_dir, self.browser_data_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        
        # 認証情報
        self.email = os.getenv("TWITCASTING_EMAIL")
        self.password = os.getenv("TWITCASTING_PASSWORD")
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("SUCCESS: グループ配信特化録画エンジン初期化完了")
    
    async def record_group_stream(self, group_url: str, password: Optional[str] = None) -> bool:
        """
        グループ配信録画実行（成功例の設計思想完全踏襲）
        
        成功の流れ:
        1. ブラウザ起動（ログイン状態維持）
        2. グループページアクセス -> broadcaster特定
        3. 配信開始待機 -> m3u8 URL検出
        4. ジャストインタイムでCookie出力
        5. yt-dlp録画開始
        """
        if not PLAYWRIGHT_AVAILABLE:
            self.logger.error("ERROR: Playwright未インストール")
            return False
        
        # URLからグループID抽出
        group_id = self._extract_group_id(group_url)
        if not group_id:
            self.logger.error(f"ERROR: 無効なグループURL: {group_url}")
            return False
        
        self.logger.info(f"RECORDING: グループ配信録画開始: {group_id}")
        
        playwright = None
        context = None
        page = None
        
        try:
            # === Step 1: ブラウザ起動（ログイン状態維持） ===
            playwright = await async_playwright().start()
            
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.browser_data_dir),
                headless=False,  # デバッグ用。本番では True
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            
            page = context.pages[0] if context.pages else await context.new_page()
            self.logger.info("SUCCESS: ブラウザ起動完了（ログイン状態維持）")
            
            # === Step 2: ログイン確認・実行 ===
            await self._ensure_login(page)
            
            # === Step 3: グループページアクセス・broadcaster特定 ===
            broadcaster_url = await self._resolve_group_broadcaster(page, group_url)
            if not broadcaster_url:
                self.logger.error("ERROR: broadcaster URL特定失敗")
                return False
            
            # === Step 4: 配信開始待機・m3u8検出 ===
            m3u8_url = await self._wait_for_stream_with_m3u8_detection(page, broadcaster_url, password)
            if not m3u8_url:
                self.logger.error("ERROR: 配信開始/m3u8検出失敗")
                return False
            
            # === Step 5: ジャストインタイムCookie出力 ===
            cookie_file = await self._export_fresh_cookies_netscape(page, group_id)
            if not cookie_file:
                self.logger.error("ERROR: Cookie出力失敗")
                return False
            
            # === Step 6: yt-dlp録画開始 ===
            success = await self._start_ytdlp_recording(m3u8_url, cookie_file, group_id)
            
            return success
            
        except Exception as e:
            self.logger.error(f"ERROR: グループ配信録画エラー: {e}", exc_info=True)
            return False
        
        finally:
            # クリーンアップ
            if page:
                await page.close()
            if context:
                await context.close()
            if playwright:
                await playwright.stop()
    
    async def _ensure_login(self, page):
        """ログイン確認・実行（成功例と同じロジック）"""
        try:
            # TwitCastingトップページで認証状況確認
            await page.goto("https://twitcasting.tv/", timeout=30000)
            
            # 既にログイン済みかチェック
            if await page.locator("a[href='/logout']").count() > 0:
                self.logger.info("SUCCESS: 既にログイン済み")
                return True
            
            # ログインページへ
            await page.goto("https://twitcasting.tv/login", timeout=30000)
            
            # 認証情報でログイン試行
            if self.email and self.password:
                await page.fill('input[name="mail"]', self.email)
                await page.fill('input[name="password"]', self.password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state('domcontentloaded', timeout=15000)
                
                if "login" not in page.url:
                    self.logger.info("SUCCESS: ログイン成功")
                    return True
            
            self.logger.warning("WARNING: ログイン情報未設定または失敗")
            return False
            
        except Exception as e:
            self.logger.error(f"ログイン処理エラー: {e}")
            return False
    
    async def _resolve_group_broadcaster(self, page, group_url: str) -> Optional[str]:
        """グループページからbroadcaster URL特定"""
        try:
            self.logger.info(f"INFO: グループページ解析: {group_url}")
            await page.goto(group_url, timeout=30000)
            
            # 年齢確認突破
            age_button = page.locator("button:visible", has_text="はい")
            if await age_button.count() > 0:
                self.logger.info("INFO: 年齢確認突破")
                await age_button.first.click()
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
            
            # broadcaster URL特定（複数パターン）
            broadcaster_patterns = [
                f"{group_url}/broadcaster",
                group_url  # グループURL自体が有効な場合
            ]
            
            for url in broadcaster_patterns:
                try:
                    await page.goto(url, timeout=15000)
                    
                    # 配信プレイヤー要素の存在確認
                    player_indicators = [
                        "div[id*='player']",
                        "video",
                        "div[class*='tw-player']"
                    ]
                    
                    for indicator in player_indicators:
                        if await page.locator(indicator).count() > 0:
                            self.logger.info(f"SUCCESS: broadcaster URL特定: {url}")
                            return url
                    
                except Exception:
                    continue
            
            self.logger.error("ERROR: broadcaster URL特定失敗")
            return None
            
        except Exception as e:
            self.logger.error(f"グループ解析エラー: {e}")
            return None
    
    async def _wait_for_stream_with_m3u8_detection(self, page, broadcaster_url: str, 
                                                  password: Optional[str] = None) -> Optional[str]:
        """配信開始待機・m3u8検出（成功例完全踏襲）"""
        try:
            await page.goto(broadcaster_url, timeout=30000)
            
            # 合言葉入力（必要な場合）
            if password:
                password_input = page.locator("input[name='password']")
                if await password_input.count() > 0:
                    self.logger.info("INFO: 合言葉入力")
                    await password_input.fill(password)
                    submit_button = page.locator("button[type='submit']")
                    await submit_button.first.click()
                    await page.wait_for_load_state('domcontentloaded', timeout=10000)
            
            self.logger.info("INFO: 配信開始待機中...")
            
            # m3u8 URL検出（成功例と同じ手法）
            m3u8_url = None
            
            async def handle_response(response):
                if ".m3u8" in response.url:
                    nonlocal m3u8_url
                    m3u8_url = response.url
                    self.logger.info(f"SUCCESS: m3u8 URL検出: {response.url}")
            
            page.on("response", handle_response)
            
            # 最大5分待機
            max_wait = 300
            wait_interval = 5
            
            for i in range(0, max_wait, wait_interval):
                if m3u8_url:
                    break
                
                # ページを少し操作して活性化（成功例と同じ）
                try:
                    await page.evaluate("window.scrollBy(0, 100)")
                    await asyncio.sleep(1)
                    await page.evaluate("window.scrollBy(0, -100)")
                except:
                    pass
                
                await asyncio.sleep(wait_interval)
                
                if i % 30 == 0:
                    self.logger.info(f"INFO: 待機中... {i//60}分{i%60}秒経過")
            
            if m3u8_url:
                self.logger.info(f"SUCCESS: m3u8 URL取得成功")
                return m3u8_url
            else:
                self.logger.error("ERROR: m3u8 URL検出タイムアウト")
                return None
                
        except Exception as e:
            self.logger.error(f"m3u8検出エラー: {e}")
            return None
    
    async def _export_fresh_cookies_netscape(self, page, group_id: str) -> Optional[Path]:
        """新鮮なCookie出力（Netscape形式・成功例完全踏襲）"""
        try:
            cookie_file = self.cookies_dir / f"{group_id}_cookies.txt"
            
            # ページからCookie取得
            cookies = await page.context.cookies()
            
            # Netscape形式で出力（成功例と同じ形式）
            with open(cookie_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# This file contains the cookies for TwitCasting authentication\n")
                
                for cookie in cookies:
                    # Netscape形式: domain \t domain_flag \t path \t secure \t expires \t name \t value
                    domain = cookie['domain']
                    domain_flag = "TRUE" if domain.startswith('.') else "FALSE"
                    path = cookie['path']
                    secure = "TRUE" if cookie['secure'] else "FALSE"
                    expires = int(cookie.get('expires', 0)) if cookie.get('expires') else 0
                    name = cookie['name']
                    value = cookie['value']
                    
                    f.write(f"{domain}\t{domain_flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
            
            self.logger.info(f"SUCCESS: 新鮮なCookie出力完了: {cookie_file}")
            return cookie_file
            
        except Exception as e:
            self.logger.error(f"Cookie出力エラー: {e}")
            return None
    
    async def _start_ytdlp_recording(self, m3u8_url: str, cookie_file: Path, group_id: str) -> bool:
        """yt-dlp録画開始（成功例完全踏襲）"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = self.temp_dir / f"{group_id}_{timestamp}.mp4"
            
            # yt-dlpコマンド構築（成功例と同じパラメータ）
            cmd = [
                'yt-dlp',
                m3u8_url,
                '--output', str(output_file),
                '--cookies', str(cookie_file),
                '--no-live-from-start',
                '--format', 'best',
                '--no-part',
                '--no-mtime'
            ]
            
            self.logger.info(f"RECORDING: yt-dlp録画開始: {group_id}")
            self.logger.debug(f"コマンド: {' '.join(cmd)}")
            
            # プロセス開始（成功例と同じ方式）
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags
            )
            
            self.logger.info(f"SUCCESS: 録画開始成功: {group_id}")
            
            # プロセス監視を同期実行（成功例踏襲）
            success = await self._monitor_recording_process(process, output_file, group_id)
            
            return success
            
        except Exception as e:
            self.logger.error(f"yt-dlp録画開始エラー: {e}")
            return False
    
    async def _monitor_recording_process(self, process, output_file: Path, group_id: str) -> bool:
        """録画プロセス監視（成功例踏襲・同期実行）"""
        try:
            self.logger.info(f"INFO: 録画プロセス監視開始: {group_id}")
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.logger.info(f"SUCCESS: 録画完了: {group_id}")
                
                # 一時ファイルから最終ディレクトリに移動
                if output_file.exists():
                    final_file = self.recordings_dir / output_file.name
                    output_file.rename(final_file)
                    self.logger.info(f"SUCCESS: ファイル移動完了: {final_file}")
                    return True
                else:
                    self.logger.error(f"ERROR: 録画ファイルが存在しません: {output_file}")
                    return False
            else:
                self.logger.error(f"ERROR: 録画失敗: {group_id} (終了コード: {process.returncode})")
                if stderr:
                    error_output = stderr.decode('utf-8', errors='ignore')
                    self.logger.error(f"エラー詳細: {error_output}")
                return False
        
        except Exception as e:
            self.logger.error(f"プロセス監視エラー: {group_id} - {e}")
            return False    
    def _extract_group_id(self, group_url: str) -> Optional[str]:
        """グループURLからID抽出"""
        try:
            import re
            match = re.search(r'/g:([0-9]+)', group_url)
            if match:
                return f"g_{match.group(1)}"
            
            # フォールバック
            return group_url.split('/')[-1].replace(':', '_')
        except:
            return None

# === 使用例・テスト関数 ===
async def test_group_recording():
    """テスト用関数"""
    recorder = GroupStreamRecorder()
    
    # テスト用グループURL
    test_url = "https://twitcasting.tv/g:117191215409354941008"
    
    success = await recorder.record_group_stream(test_url)
    if success:
        print("SUCCESS: グループ配信録画テスト成功")
    else:
        print("ERROR: グループ配信録画テスト失敗")

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 環境変数設定
    os.environ.setdefault("TWITCASTING_EMAIL", "hirochi73@ezweb.ne.jp")
    os.environ.setdefault("TWITCASTING_PASSWORD", "rimrimrim999")
    
    # 実行方法
    print("USAGE: 使用方法:")
    print("python group_stream_recorder.py")
    print("または")
    print("from group_stream_recorder import GroupStreamRecorder")
    print("recorder = GroupStreamRecorder()")
    print("await recorder.record_group_stream('https://twitcasting.tv/g:117191215409354941008')")
    
    asyncio.run(test_group_recording())