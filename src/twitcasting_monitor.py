#!/usr/bin/env python3
"""
twitcasting_monitor.py - 実TwitCasting監視システム
実際のAPIチェックと録画制御
"""

import logging
import asyncio
import aiohttp
import threading
import time
import re
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from enum import Enum
from bs4 import BeautifulSoup

class StreamStatus(Enum):
    """配信状態"""
    OFFLINE = "offline"
    LIVE = "live"
    LIMITED = "limited"
    PRIVATE = "private"
    ERROR = "error"
    UNKNOWN = "unknown"

class TwitCastingMonitor:
    """実TwitCasting監視システム"""
    
    def __init__(self, config_manager, auth_manager=None, recording_engine=None):
        self.config_manager = config_manager
        self.auth_manager = auth_manager
        self.recording_engine = recording_engine
        self.logger = logging.getLogger(__name__)
        
        # 設定取得
        self.system_config = config_manager.get_system_config()
        
        # 監視対象
        self.monitored_streams: Dict[str, Dict[str, Any]] = {}
        self.stream_states: Dict[str, Dict[str, Any]] = {}
        
        # 監視制御
        self.monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
        
        # HTTP関連
        self.session: Optional[aiohttp.ClientSession] = None
        self.check_interval = 30  # 30秒間隔
        
        # 統計
        self.check_count = 0
        self.last_check_time = None
        self.error_count = 0
        
        self.logger.info("実TwitCasting監視システム初期化完了")
    
    async def _ensure_session(self):
        """HTTPセッション確保"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=10)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
    
    def add_stream(self, url: str, password: Optional[str] = None):
        """監視対象追加"""
        with self._lock:
            username = self._extract_username(url)
            self.monitored_streams[url] = {
                'url': url,
                'username': username,
                'password': password,
                'added_at': datetime.now().isoformat(),
                'check_count': 0,
                'last_status': StreamStatus.UNKNOWN.value,
                'last_error': None
            }
            
            # 初期状態設定
            self.stream_states[url] = {
                'status': StreamStatus.UNKNOWN.value,
                'username': username,
                'last_check': None,
                'check_count': 0,
                'title': '',
                'viewer_count': 0,
                'is_limited': False,
                'thumbnail_url': '',
                'recording': False
            }
            
            self.logger.info(f"監視対象追加: {username}")
    
    def remove_stream(self, url: str) -> bool:
        """監視対象削除"""
        with self._lock:
            if url in self.monitored_streams:
                username = self.monitored_streams[url]['username']
                del self.monitored_streams[url]
                
                if url in self.stream_states:
                    del self.stream_states[url]
                
                self.logger.info(f"監視対象削除: {username}")
                return True
            return False
    
    def start_monitoring(self):
        """監視開始"""
        if self.monitoring:
            self.logger.warning("既に監視中です")
            return
        
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info("実監視開始")
    
    def stop_monitoring(self):
        """監視停止"""
        if not self.monitoring:
            self.logger.warning("監視していません")
            return
        
        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
        
        self.logger.info("監視停止")
    
    async def _monitor_loop(self):
        """監視ループ"""
        self.logger.info("監視ループ開始")
        
        try:
            await self._ensure_session()
            
            while self.monitoring:
                try:
                    await self._check_all_streams()
                    await asyncio.sleep(self.check_interval)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"監視ループエラー: {e}")
                    self.error_count += 1
                    await asyncio.sleep(60)  # エラー時は長めに待機
        
        except Exception as e:
            self.logger.error(f"監視ループ致命的エラー: {e}")
        
        finally:
            await self._cleanup_session()
            self.logger.info("監視ループ終了")
    
    async def _check_all_streams(self):
        """全配信チェック"""
        self.check_count += 1
        self.last_check_time = datetime.now().isoformat()
        
        # 並列チェック（ただし同時接続数を制限）
        semaphore = asyncio.Semaphore(3)  # 最大3同時接続
        
        tasks = []
        for url, stream_info in list(self.monitored_streams.items()):
            task = asyncio.create_task(self._check_stream_with_semaphore(semaphore, url, stream_info))
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _check_stream_with_semaphore(self, semaphore: asyncio.Semaphore, url: str, stream_info: Dict[str, Any]):
        """セマフォ付き配信チェック"""
        async with semaphore:
            try:
                await self._check_stream(url, stream_info)
            except Exception as e:
                self.logger.error(f"配信チェックエラー ({url}): {e}")
                await self._update_stream_error(url, str(e))
    
    async def _check_stream(self, url: str, stream_info: Dict[str, Any]):
        """個別配信チェック"""
        username = stream_info['username']
        
        try:
            # TwitCastingページを取得
            stream_data = await self._fetch_stream_data(url)
            
            # 状態判定
            status = self._determine_stream_status(stream_data)
            
            # 状態更新
            old_status = self.stream_states[url]['status']
            await self._update_stream_state(url, status, stream_data)
            
            # 録画制御
            if old_status != status.value:
                await self._handle_status_change(url, old_status, status.value)
            
            # 統計更新
            stream_info['check_count'] += 1
            stream_info['last_status'] = status.value
            stream_info['last_error'] = None
            
        except Exception as e:
            self.logger.error(f"配信チェック失敗: {username} - {e}")
            await self._update_stream_error(url, str(e))
    
    async def _fetch_stream_data(self, url: str) -> Dict[str, Any]:
        """配信データ取得"""
        await self._ensure_session()
        
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                
                html = await response.text()
                return self._parse_stream_page(html, url)
                
        except Exception as e:
            raise Exception(f"ページ取得失敗: {e}")
    
    def _parse_stream_page(self, html: str, url: str) -> Dict[str, Any]:
        """配信ページ解析"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            data = {
                'title': '',
                'viewer_count': 0,
                'is_live': False,
                'is_limited': False,
                'is_private': False,
                'thumbnail_url': '',
                'raw_html': html
            }
            
            # ライブ配信チェック
            live_indicators = [
                soup.find('span', class_='tw-player-status-live'),
                soup.find('div', class_='tw-player-live-indicator'),
                soup.find('meta', property='og:video:url'),
                'LIVE' in html.upper(),
                'ライブ' in html
            ]
            data['is_live'] = any(live_indicators)
            
            # 限定配信チェック
            limited_indicators = [
                '限定配信' in html,
                'limited' in html.lower(),
                'password' in html.lower(),
                soup.find('input', {'type': 'password'}),
                'コメント・録画・配信禁止' in html
            ]
            data['is_limited'] = any(limited_indicators)
            
            # プライベート配信チェック
            private_indicators = [
                'プライベート配信' in html,
                'private' in html.lower(),
                'この配信は限定公開されています' in html
            ]
            data['is_private'] = any(private_indicators)
            
            # タイトル取得
            title_selectors = [
                ('meta', {'property': 'og:title'}),
                ('title', {}),
                ('.tw-player-title', {}),
                ('h1', {})
            ]
            
            for selector, attrs in title_selectors:
                if selector == 'meta':
                    element = soup.find('meta', attrs)
                    if element and element.get('content'):
                        data['title'] = element['content'].strip()
                        break
                else:
                    element = soup.find(selector, attrs)
                    if element and element.get_text():
                        data['title'] = element.get_text().strip()
                        break
            
            # 視聴者数取得（概算）
            viewer_patterns = [
                r'(\d+)\s*人が視聴中',
                r'(\d+)\s*viewers?',
                r'視聴者.*?(\d+)'
            ]
            
            for pattern in viewer_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    try:
                        data['viewer_count'] = int(match.group(1))
                        break
                    except:
                        pass
            
            # サムネイル取得
            thumbnail_element = soup.find('meta', property='og:image')
            if thumbnail_element and thumbnail_element.get('content'):
                data['thumbnail_url'] = thumbnail_element['content']
            
            return data
            
        except Exception as e:
            raise Exception(f"ページ解析エラー: {e}")
    
    def _determine_stream_status(self, stream_data: Dict[str, Any]) -> StreamStatus:
        """配信状態判定"""
        try:
            if stream_data.get('is_private'):
                return StreamStatus.PRIVATE
            elif stream_data.get('is_limited'):
                return StreamStatus.LIMITED
            elif stream_data.get('is_live'):
                return StreamStatus.LIVE
            else:
                return StreamStatus.OFFLINE
                
        except Exception as e:
            self.logger.error(f"状態判定エラー: {e}")
            return StreamStatus.ERROR
    
    async def _update_stream_state(self, url: str, status: StreamStatus, stream_data: Dict[str, Any]):
        """配信状態更新"""
        with self._lock:
            if url in self.stream_states:
                self.stream_states[url].update({
                    'status': status.value,
                    'last_check': datetime.now().isoformat(),
                    'check_count': self.stream_states[url]['check_count'] + 1,
                    'title': stream_data.get('title', ''),
                    'viewer_count': stream_data.get('viewer_count', 0),
                    'is_limited': stream_data.get('is_limited', False),
                    'thumbnail_url': stream_data.get('thumbnail_url', ''),
                    'recording': self.recording_engine.is_recording(url) if self.recording_engine else False
                })
    
    async def _update_stream_error(self, url: str, error: str):
        """配信エラー更新"""
        with self._lock:
            if url in self.stream_states:
                self.stream_states[url].update({
                    'status': StreamStatus.ERROR.value,
                    'last_check': datetime.now().isoformat(),
                    'last_error': error
                })
            
            if url in self.monitored_streams:
                self.monitored_streams[url]['last_error'] = error
    
    async def _handle_status_change(self, url: str, old_status: str, new_status: str):
        """状態変化処理"""
        if not self.recording_engine:
            self.logger.warning("録画エンジンが利用できません")
            return
        
        username = self._extract_username(url)
        password = self.monitored_streams[url].get('password')
        
        self.logger.info(f"配信状態変化: {username} {old_status} -> {new_status}")
        
        # 録画開始条件
        if new_status in ['live', 'limited'] and old_status not in ['live', 'limited']:
            if not self.recording_engine.is_recording(url):
                self.logger.info(f"🔴 録画開始トリガー: {username}")
                
                # 限定配信の場合、パスワードが必要
                if new_status == 'limited' and not password:
                    self.logger.warning(f"⚠️ 限定配信ですがパスワードが設定されていません: {username}")
                
                try:
                    success = await self.recording_engine.start_recording(url, password)
                    if success:
                        self.logger.info(f"✅ 録画開始成功: {username}")
                        
                        # 状態を録画中に更新
                        with self._lock:
                            if url in self.stream_states:
                                self.stream_states[url]['recording'] = True
                    else:
                        self.logger.error(f"❌ 録画開始失敗: {username}")
                        
                except Exception as e:
                    self.logger.error(f"❌ 録画開始例外: {username} - {e}")
        
        # 録画停止条件
        elif new_status == 'offline' and old_status in ['live', 'limited']:
            if self.recording_engine.is_recording(url):
                self.logger.info(f"⏹️ 録画停止トリガー: {username}")
                
                try:
                    success = await self.recording_engine.stop_recording(url)
                    if success:
                        self.logger.info(f"✅ 録画停止成功: {username}")
                        
                        # 状態を非録画に更新
                        with self._lock:
                            if url in self.stream_states:
                                self.stream_states[url]['recording'] = False
                    else:
                        self.logger.error(f"❌ 録画停止失敗: {username}")
                        
                except Exception as e:
                    self.logger.error(f"❌ 録画停止例外: {username} - {e}")
        
        # エラー状態での録画停止
        elif new_status == 'error' and self.recording_engine.is_recording(url):
            self.logger.warning(f"⚠️ エラー状態のため録画停止: {username}")
            try:
                await self.recording_engine.stop_recording(url)
                with self._lock:
                    if url in self.stream_states:
                        self.stream_states[url]['recording'] = False
            except Exception as e:
                self.logger.error(f"❌ エラー時録画停止失敗: {username} - {e}")
    
    async def _cleanup_session(self):
        """HTTPセッションクリーンアップ"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
    
    def _extract_username(self, url: str) -> str:
        """URLからユーザー名抽出"""
        try:
            return url.rstrip('/').split('/')[-1]
        except:
            return "unknown"
    
    def get_stream_states(self) -> Dict[str, Dict[str, Any]]:
        """配信状態一覧取得"""
        with self._lock:
            return {url: state.copy() for url, state in self.stream_states.items()}
    
    def get_monitoring_statistics(self) -> Dict[str, Any]:
        """監視統計取得"""
        states = self.get_stream_states()
        
        live_count = len([s for s in states.values() if s['status'] == 'live'])
        limited_count = len([s for s in states.values() if s['status'] == 'limited'])
        offline_count = len([s for s in states.values() if s['status'] == 'offline'])
        recording_count = len([s for s in states.values() if s.get('recording', False)])
        
        return {
            'total_streams': len(self.monitored_streams),
            'live_streams': live_count,
            'limited_streams': limited_count,
            'offline_streams': offline_count,
            'error_streams': len([s for s in states.values() if s['status'] == 'error']),
            'recording_streams': recording_count,
            'total_checks': self.check_count,
            'error_count': self.error_count,
            'last_check': self.last_check_time,
            'monitoring': self.monitoring,
            'check_interval': self.check_interval
        }
    
    def get_detailed_status(self) -> Dict[str, Any]:
        """詳細状態取得"""
        states = self.get_stream_states()
        detailed = {}
        
        for url, state in states.items():
            username = state['username']
            detailed[username] = {
                'url': url,
                'status': state['status'],
                'title': state.get('title', ''),
                'viewer_count': state.get('viewer_count', 0),
                'is_limited': state.get('is_limited', False),
                'recording': state.get('recording', False),
                'last_check': state.get('last_check'),
                'check_count': state.get('check_count', 0)
            }
        
        return detailed
    
    async def force_check_stream(self, url: str) -> Dict[str, Any]:
        """特定配信の強制チェック"""
        if url not in self.monitored_streams:
            raise ValueError(f"監視対象ではありません: {url}")
        
        stream_info = self.monitored_streams[url]
        
        try:
            await self._check_stream(url, stream_info)
            return self.stream_states[url].copy()
        except Exception as e:
            self.logger.error(f"強制チェック失敗: {url} - {e}")
            raise
    
    async def update_stream_password(self, url: str, password: Optional[str]):
        """配信パスワード更新"""
        if url in self.monitored_streams:
            self.monitored_streams[url]['password'] = password
            username = self.monitored_streams[url]['username']
            self.logger.info(f"パスワード更新: {username}")
        else:
            raise ValueError(f"監視対象ではありません: {url}")
    
    def __del__(self):
        """デストラクタ"""
        if hasattr(self, 'session') and self.session and not self.session.closed:
            asyncio.create_task(self._cleanup_session())