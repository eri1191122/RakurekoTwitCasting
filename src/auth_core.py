#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auth_core.py - 認証・Cookie管理システム（Phase 1修正版）
限定配信対応の核心モジュール
"""

import asyncio
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from http.cookiejar import MozillaCookieJar, Cookie
import logging

# 依存関係インポート
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import NoSuchElementException, TimeoutException
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

logger = logging.getLogger(__name__)

class TwitCastingAuth:
    """TwitCasting認証管理（限定配信対応）"""
    
    def __init__(self, base_dir: str = None):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.cookies_json = self.base_dir / "twitcasting_cookies.json"
        self.cookies_txt = self.base_dir / "twitcasting_cookies.txt"
        self.user_data_dir = self.base_dir / "playwright_user_data"
        
        # 認証情報
        self.email = os.getenv("TWITCASTING_EMAIL")
        self.password = os.getenv("TWITCASTING_PASSWORD")
        self.user_id = os.getenv("TWITCASTING_ID")
        
        # 設定
        self.cookie_refresh_hours = 24
        self.last_refresh = None
        
    def needs_refresh(self) -> bool:
        """Cookie更新が必要かチェック"""
        if not self.cookies_json.exists():
            return True
            
        try:
            file_time = datetime.fromtimestamp(self.cookies_json.stat().st_mtime)
            return datetime.now() - file_time > timedelta(hours=self.cookie_refresh_hours)
        except Exception:
            return True
    
    def get_cookie_string(self) -> str:
        """Cookie文字列取得（streamlink/yt-dlp用）"""
        try:
            if self.cookies_json.exists():
                with open(self.cookies_json, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                return "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        except Exception as e:
            logger.error(f"Cookie文字列取得エラー: {e}")
        return ""
    
    # ✅ 修正: 不足していたget_cookiesメソッド追加
    def get_cookies(self) -> Optional[List[Dict]]:
        """Cookie情報取得"""
        try:
            if self.cookies_json.exists():
                with open(self.cookies_json, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Cookie取得エラー: {e}")
        return None
    
    def get_netscape_cookies_path(self) -> str:
        """Netscape形式cookiesファイルパス取得"""
        return str(self.cookies_txt)
    
    # ✅ 修正: Cookieファイル存在確認メソッド追加
    def has_valid_cookies(self) -> bool:
        """有効なCookieファイルが存在するかチェック"""
        return (self.cookies_json.exists() and 
                self.cookies_txt.exists() and 
                not self.needs_refresh())
    
    async def refresh_cookies_playwright(self, headless: bool = True) -> bool:
        """Playwright使用のCookie更新（限定配信対応）"""
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright未インストール")
            return False
        
        try:
            self.user_data_dir.mkdir(exist_ok=True)
            
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=headless,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )
                
                page = context.pages[0] if context.pages else await context.new_page()
                
                # ログインページへ移動
                await page.goto("https://twitcasting.tv/login", timeout=30000)
                
                # 既にログイン済みかチェック
                if "login" not in page.url:
                    logger.info("既にログイン済み")
                else:
                    # ログイン実行
                    success = await self._perform_login_playwright(page)
                    if not success:
                        await context.close()
                        return False
                
                # Cookie取得・保存
                cookies = await context.cookies()
                success = self._save_cookies(cookies)
                
                await context.close()
                return success
                
        except Exception as e:
            logger.error(f"Playwright Cookie更新エラー: {e}")
            return False
    
    async def _perform_login_playwright(self, page) -> bool:
        """Playwrightでのログイン実行"""
        try:
            # メールアドレスでのログイン試行
            if self.email and self.password:
                await page.fill('input[name="mail"]', self.email)
                await page.fill('input[name="password"]', self.password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
                
                if "login" not in page.url:
                    logger.info("メールアドレスでのログイン成功")
                    return True
            
            # ユーザーIDでのログイン試行
            if self.user_id and self.password:
                await page.goto("https://twitcasting.tv/login")
                await page.fill('input[name="user_id"]', self.user_id)
                await page.fill('input[name="password"]', self.password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
                
                if "login" not in page.url:
                    logger.info("ユーザーIDでのログイン成功")
                    return True
            
            logger.error("全てのログイン方法が失敗")
            return False
            
        except Exception as e:
            logger.error(f"ログイン実行エラー: {e}")
            return False
    
    def refresh_cookies_selenium(self, headless: bool = True) -> bool:
        """Selenium使用のCookie更新（フォールバック）"""
        if not SELENIUM_AVAILABLE:
            logger.error("Selenium未インストール")
            return False
        
        driver = None
        try:
            # WebDriver設定
            options = Options()
            if headless:
                options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(30)
            
            # ログインページへ移動
            driver.get("https://twitcasting.tv/login")
            
            # ログイン試行
            login_success = False
            
            # メールアドレスでのログイン
            if self.email and self.password:
                login_success = self._attempt_login_selenium(driver, "mail", self.email, self.password)
            
            # ユーザーIDでのログイン
            if not login_success and self.user_id and self.password:
                driver.get("https://twitcasting.tv/login")
                time.sleep(2)
                login_success = self._attempt_login_selenium(driver, "user_id", self.user_id, self.password)
            
            if not login_success:
                return False
            
            # Cookie取得・保存
            cookies = driver.get_cookies()
            return self._save_cookies(cookies)
            
        except Exception as e:
            logger.error(f"Selenium Cookie更新エラー: {e}")
            return False
        finally:
            if driver:
                driver.quit()
    
    def _attempt_login_selenium(self, driver, field_name: str, login_value: str, password: str) -> bool:
        """Seleniumでのログイン試行"""
        try:
            login_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, field_name))
            )
            login_field.clear()
            login_field.send_keys(login_value)
            
            password_field = driver.find_element(By.NAME, "password")
            password_field.clear()
            password_field.send_keys(password)
            
            login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            login_button.click()
            
            time.sleep(3)
            
            # エラーチェック
            try:
                error_element = driver.find_element(By.CLASS_NAME, "tw-login-error")
                if error_element.is_displayed():
                    logger.warning(f"{field_name}ログイン失敗: {error_element.text}")
                    return False
            except NoSuchElementException:
                pass
            
            # URL変化チェック
            if "login" not in driver.current_url:
                logger.info(f"{field_name}ログイン成功")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"ログイン試行エラー ({field_name}): {e}")
            return False
    
    def _save_cookies(self, cookies: List[Dict]) -> bool:
        """Cookie保存（JSON + Netscape形式）"""
        try:
            # JSON形式保存
            with open(self.cookies_json, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, indent=2, ensure_ascii=False)
            
            # Netscape形式保存
            cookie_jar = MozillaCookieJar(str(self.cookies_txt))
            
            for cookie in cookies:
                expires_val = cookie.get('expiry', None)
                if expires_val is not None:
                    expires_val = int(expires_val)
                
                c = Cookie(
                    version=0,
                    name=cookie['name'],
                    value=cookie['value'],
                    port=None,
                    port_specified=False,
                    domain=cookie['domain'],
                    domain_specified=True,
                    domain_initial_dot=cookie['domain'].startswith('.'),
                    path=cookie['path'],
                    path_specified=True,
                    secure=cookie['secure'],
                    expires=expires_val,
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False
                )
                cookie_jar.set_cookie(c)
            
            cookie_jar.save(ignore_discard=True, ignore_expires=True)
            
            self.last_refresh = datetime.now()
            logger.info("Cookie保存完了")
            return True
            
        except Exception as e:
            logger.error(f"Cookie保存エラー: {e}")
            return False
    
    async def handle_stream_barriers(self, page, password: str = None) -> bool:
        """配信の障壁突破（年齢制限・合言葉）"""
        try:
            # 年齢確認
            age_button = page.locator("button:visible", has_text="はい")
            if await age_button.count() > 0:
                logger.info("年齢確認画面を突破")
                await age_button.first.click()
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
            
            # 合言葉入力
            password_input = page.locator("input[name='password']")
            if await password_input.count() > 0:
                if password:
                    logger.info("合言葉を入力")
                    await password_input.fill(password)
                    submit_button = page.locator("button[type='submit']")
                    await submit_button.first.click()
                    await page.wait_for_load_state('domcontentloaded', timeout=10000)
                else:
                    logger.warning("合言葉が必要ですが設定されていません")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"障壁突破エラー: {e}")
            return False
    
    async def auto_refresh_if_needed(self, headless: bool = True) -> bool:
        """必要に応じて自動Cookie更新"""
        if not self.needs_refresh():
            return True
        
        logger.info("Cookie更新が必要です")
        
        # Playwrightを優先、失敗時はSeleniumフォールバック
        success = await self.refresh_cookies_playwright(headless)
        if not success and SELENIUM_AVAILABLE:
            logger.info("Playwrightが失敗、Seleniumで再試行")
            success = self.refresh_cookies_selenium(headless)
        
        if success:
            logger.info("Cookie更新成功")
        else:
            logger.error("Cookie更新失敗")
        
        return success


class LimitedStreamAuth:
    """限定配信専用認証（メンバーシップ・年齢制限対応）"""
    
    def __init__(self, auth_manager: TwitCastingAuth):
        self.auth = auth_manager
        self.stream_passwords = {}  # URL別合言葉
    
    def set_stream_password(self, url: str, password: str):
        """配信URL別の合言葉設定"""
        self.stream_passwords[url] = password
    
    async def authenticate_for_stream(self, url: str, headless: bool = True) -> Dict:
        """特定配信の認証実行（Phase 1修正版）"""
        if not PLAYWRIGHT_AVAILABLE:
            return {"success": False, "error": "Playwright未対応"}
        
        try:
            # ✅ 修正: Cookie事前確認と更新
            cookie_updated = await self.auth.auto_refresh_if_needed(headless)
            if not cookie_updated:
                logger.warning("Cookie更新に失敗しましたが処理を続行します")
            
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=self.auth.user_data_dir,
                    headless=headless,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )
                
                page = context.pages[0] if context.pages else await context.new_page()
                
                # 配信ページへ移動
                logger.info(f"🔗 配信ページへアクセス: {url}")
                await page.goto(url, timeout=30000)
                
                # 障壁突破
                password = self.stream_passwords.get(url)
                barrier_success = await self.auth.handle_stream_barriers(page, password)
                
                if not barrier_success:
                    await context.close()
                    return {"success": False, "error": "障壁突破失敗"}
                
                # ✅ 修正: m3u8検出の改良版
                logger.info("📡 配信開始/m3u8検出を待機中...")
                m3u8_url = None
                
                try:
                    # レスポンス監視でm3u8を検出
                    async def handle_response(response):
                        if ".m3u8" in response.url:
                            nonlocal m3u8_url
                            m3u8_url = response.url
                            logger.info(f"🎯 m3u8 URL検出: {response.url}")
                    
                    page.on("response", handle_response)
                    
                    # 最大5分待機
                    max_wait = 300  # 5分
                    wait_interval = 5  # 5秒間隔
                    
                    for i in range(0, max_wait, wait_interval):
                        if m3u8_url:
                            break
                        
                        # ページを少しスクロールして活性化
                        try:
                            await page.evaluate("window.scrollBy(0, 100)")
                            await asyncio.sleep(1)
                            await page.evaluate("window.scrollBy(0, -100)")
                        except:
                            pass
                        
                        await asyncio.sleep(wait_interval)
                        
                        if i % 30 == 0:  # 30秒ごとにログ
                            logger.info(f"⏳ 待機中... {i//60}分{i%60}秒経過")
                    
                    if not m3u8_url:
                        # フォールバック: 直接URLを使用
                        logger.warning("m3u8検出失敗、元URLを使用します")
                        m3u8_url = url
                    
                    # 最新Cookie取得
                    cookies = await context.cookies()
                    cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                    
                    await context.close()
                    
                    return {
                        "success": True,
                        "m3u8_url": m3u8_url,
                        "cookie_header": cookie_header,
                        "timestamp": datetime.now().isoformat(),
                        "detected_via": "m3u8_response" if ".m3u8" in m3u8_url else "fallback_url"
                    }
                    
                except Exception as e:
                    await context.close()
                    logger.error(f"m3u8検出処理エラー: {e}")
                    return {"success": False, "error": f"m3u8検出エラー: {e}"}
        
        except Exception as e:
            logger.error(f"配信認証エラー: {e}")
            return {"success": False, "error": str(e)}


# ✅ 修正: 簡易テスト機能追加
async def test_auth_system():
    """認証システムテスト"""
    print("🔐 認証システムテスト開始")
    
    try:
        # 基本認証管理のテスト
        auth = TwitCastingAuth()
        print(f"📁 Cookie保存先: {auth.cookies_json}")
        print(f"🍪 Cookie更新必要: {auth.needs_refresh()}")
        print(f"✅ Cookie文字列長: {len(auth.get_cookie_string())}")
        
        # 限定配信認証のテスト準備
        limited_auth = LimitedStreamAuth(auth)
        print("🎯 限定配信認証準備完了")
        
        # 依存関係チェック
        print(f"🎭 Playwright利用可能: {PLAYWRIGHT_AVAILABLE}")
        print(f"🚗 Selenium利用可能: {SELENIUM_AVAILABLE}")
        
        print("✅ 認証システム基本テスト完了")
        return True
        
    except Exception as e:
        print(f"❌ 認証システムテストエラー: {e}")
        return False


# === 使用例 ===
async def main_example():
    """使用例"""
    # 基本認証管理
    auth = TwitCastingAuth()
    
    # Cookie更新（必要に応じて）
    await auth.auto_refresh_if_needed(headless=True)
    
    # 限定配信認証
    limited_auth = LimitedStreamAuth(auth)
    limited_auth.set_stream_password("https://twitcasting.tv/example", "password123")
    
    # 配信認証実行
    result = await limited_auth.authenticate_for_stream("https://twitcasting.tv/example")
    
    if result["success"]:
        print(f"認証成功: {result['m3u8_url']}")
        print(f"Cookie: {result['cookie_header']}")
    else:
        print(f"認証失敗: {result['error']}")

if __name__ == "__main__":
    import asyncio
    # テスト実行
    asyncio.run(test_auth_system())