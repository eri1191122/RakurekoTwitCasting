#!/usr/bin/env python3
"""
recording_engine.py - 実録画エンジン
Streamlinkを使用した本格的な録画システム
"""

import os
import logging
import asyncio
import subprocess
import threading
import time
import json
import signal
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import platform

class RecordingMethod(Enum):
    """録画方式"""
    STREAMLINK = "streamlink"
    YT_DLP = "yt-dlp"

class RecordingStatus(Enum):
    """録画状態"""
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class RecordingEngine:
    """実録画エンジン"""
    
    def __init__(self, config_manager, auth_manager=None):
        self.config_manager = config_manager
        self.auth_manager = auth_manager
        self.logger = logging.getLogger(__name__)
        
        # 設定取得
        self.system_config = config_manager.get_system_config()
        self.recording_config = config_manager.get_recording_config()
        
        # 録画管理
        self.active_recordings: Dict[str, Dict[str, Any]] = {}
        self.completed_recordings: List[Dict[str, Any]] = []
        self.failed_recordings: List[Dict[str, Any]] = []
        
        # 制御
        self.shutdown_requested = False
        self._lock = threading.Lock()
        
        # 出力ディレクトリ作成
        self._ensure_directories()
        
        self.logger.info("実録画エンジン初期化完了")
    
    def _ensure_directories(self):
        """必要なディレクトリを作成"""
        try:
            # 基本ディレクトリ
            self.recordings_dir = self.system_config.recordings_dir / "videos"
            self.temp_dir = self.system_config.recordings_dir / "temp"
            self.converted_dir = self.system_config.recordings_dir / "converted"
            
            # ディレクトリ作成
            for directory in [self.recordings_dir, self.temp_dir, self.converted_dir]:
                directory.mkdir(parents=True, exist_ok=True)
            
            self.logger.info(f"録画ディレクトリ準備完了: {self.recordings_dir}")
            
        except Exception as e:
            self.logger.error(f"ディレクトリ作成エラー: {e}")
            raise
    
    async def start_recording(self, url: str, password: Optional[str] = None, 
                            method: RecordingMethod = RecordingMethod.STREAMLINK) -> bool:
        """録画開始"""
        try:
            username = self._extract_username(url)
            
            # 重複チェック
            if self.is_recording(url):
                self.logger.warning(f"既に録画中: {username}")
                return False
            
            # ファイル名生成
            filename = self._generate_filename(username)
            output_path = self.recordings_dir / filename
            temp_path = self.temp_dir / filename
            
            # 録画情報作成
            recording_info = {
                'url': url,
                'username': username,
                'password': password,
                'method': method.value,
                'status': RecordingStatus.STARTING.value,
                'start_time': datetime.now().isoformat(),
                'output_path': str(output_path),
                'temp_path': str(temp_path),
                'process': None,
                'file_size': 0,
                'duration': 0
            }
            
            with self._lock:
                self.active_recordings[url] = recording_info
            
            # 非同期で録画開始
            asyncio.create_task(self._run_recording(url, recording_info))
            
            self.logger.info(f"✅ 録画開始: {username}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 録画開始エラー: {url} - {e}")
            return False
    
    async def _run_recording(self, url: str, recording_info: Dict[str, Any]):
        """録画実行"""
        username = recording_info['username']
        method = recording_info['method']
        
        try:
            # 録画コマンド準備
            if method == RecordingMethod.STREAMLINK.value:
                success = await self._record_with_streamlink(url, recording_info)
            elif method == RecordingMethod.YT_DLP.value:
                success = await self._record_with_ytdlp(url, recording_info)
            else:
                raise ValueError(f"未対応の録画方式: {method}")
            
            # 録画完了処理
            await self._finalize_recording(url, recording_info, success)
            
        except Exception as e:
            self.logger.error(f"録画実行エラー: {username} - {e}")
            await self._finalize_recording(url, recording_info, False)
    
    async def _record_with_streamlink(self, url: str, recording_info: Dict[str, Any]) -> bool:
        """Streamlinkによる録画"""
        username = recording_info['username']
        temp_path = recording_info['temp_path']
        password = recording_info.get('password')
        
        try:
            # 基本コマンド構築（シンプル版）
            cmd = [
                'streamlink',
                url,
                self.recording_config.video_quality,
                '--output', temp_path,
                '--force',
                '--quiet',
                '--retry-streams', '3'
            ]
            
            # 年齢制限・限定配信対応（Cookie使用）
            if self.auth_manager and hasattr(self.auth_manager, 'get_cookies'):
                cookies = self.auth_manager.get_cookies()
                if cookies:
                    # Cookieファイルが存在する場合
                    cookie_file = self.system_config.data_dir / "cookies" / "twitcasting_cookies.txt"
                    if cookie_file.exists():
                        cmd.extend(['--http-cookie-file', str(cookie_file)])
                        self.logger.info(f"Cookie使用: {username}")
            
            # 限定配信パスワード対応（将来実装）
            if password:
                self.logger.warning(f"限定配信パスワード検出（未対応）: {username}")
            
            # プロセス作成フラグ（Windows対応）
            creation_flags = 0
            if platform.system() == "Windows":
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            self.logger.info(f"Streamlinkコマンド: {' '.join(cmd)}")
            
            # プロセス開始
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags
            )
            
            # 録画情報更新
            with self._lock:
                recording_info['process'] = process
                recording_info['status'] = RecordingStatus.RECORDING.value
            
            self.logger.info(f"Streamlink録画開始: {username}")
            
            # プロセス監視
            return await self._monitor_recording_process(process, recording_info)
            
        except Exception as e:
            self.logger.error(f"Streamlink録画エラー: {username} - {e}")
            return False
    
    async def _record_with_ytdlp(self, url: str, recording_info: Dict[str, Any]) -> bool:
        """yt-dlpによる録画"""
        username = recording_info['username']
        temp_path = recording_info['temp_path']
        
        try:
            # コマンド構築
            cmd = [
                'yt-dlp',
                url,
                '--output', temp_path,
                '--format', 'best',
                '--live-from-start',
                '--no-part'
            ]
            
            # プロセス作成フラグ（Windows対応）
            creation_flags = 0
            if platform.system() == "Windows":
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            # プロセス開始
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags
            )
            
            # 録画情報更新
            with self._lock:
                recording_info['process'] = process
                recording_info['status'] = RecordingStatus.RECORDING.value
            
            self.logger.info(f"yt-dlp録画開始: {username}")
            
            # プロセス監視
            return await self._monitor_recording_process(process, recording_info)
            
        except Exception as e:
            self.logger.error(f"yt-dlp録画エラー: {username} - {e}")
            return False
    
    async def _monitor_recording_process(self, process, recording_info: Dict[str, Any]) -> bool:
        """録画プロセス監視"""
        username = recording_info['username']
        
        try:
            # プロセス完了を待機（タイムアウト対応）
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.system_config.recording_timeout_minutes * 60
                )
                
                return_code = process.returncode
                
                if return_code == 0:
                    self.logger.info(f"録画正常終了: {username}")
                    return True
                else:
                    self.logger.error(f"録画異常終了: {username} (終了コード: {return_code})")
                    if stderr:
                        self.logger.error(f"エラー出力: {stderr.decode('utf-8', errors='ignore')}")
                    return False
                    
            except asyncio.TimeoutError:
                self.logger.warning(f"録画タイムアウト: {username}")
                process.kill()
                await process.wait()
                return False
                
        except Exception as e:
            self.logger.error(f"プロセス監視エラー: {username} - {e}")
            return False
    
    async def _finalize_recording(self, url: str, recording_info: Dict[str, Any], success: bool):
        """録画完了処理"""
        username = recording_info['username']
        temp_path = Path(recording_info['temp_path'])
        output_path = Path(recording_info['output_path'])
        
        try:
            # 録画情報更新
            recording_info['end_time'] = datetime.now().isoformat()
            
            if success and temp_path.exists():
                # ファイル移動
                if temp_path != output_path:
                    temp_path.replace(output_path)
                
                # ファイル情報更新
                if output_path.exists():
                    recording_info['file_size'] = output_path.stat().st_size
                    recording_info['final_path'] = str(output_path)
                
                recording_info['status'] = RecordingStatus.COMPLETED.value
                
                # 完了リストに追加
                with self._lock:
                    if url in self.active_recordings:
                        del self.active_recordings[url]
                    self.completed_recordings.append(recording_info.copy())
                
                self.logger.info(f"✅ 録画完了: {username} ({self._format_file_size(recording_info['file_size'])})")
                
                # 後処理（変換等）
                if self.recording_config.auto_convert:
                    asyncio.create_task(self._post_process_recording(recording_info))
            
            else:
                recording_info['status'] = RecordingStatus.FAILED.value
                
                # 失敗リストに追加
                with self._lock:
                    if url in self.active_recordings:
                        del self.active_recordings[url]
                    self.failed_recordings.append(recording_info.copy())
                
                # 一時ファイル削除
                if temp_path.exists():
                    temp_path.unlink()
                
                self.logger.error(f"❌ 録画失敗: {username}")
                
        except Exception as e:
            self.logger.error(f"録画完了処理エラー: {username} - {e}")
    
    async def _post_process_recording(self, recording_info: Dict[str, Any]):
        """録画後処理"""
        try:
            output_path = Path(recording_info['final_path'])
            
            if self.recording_config.auto_convert and self.recording_config.convert_format != 'mp4':
                await self._convert_recording(recording_info)
            
            self.logger.info(f"後処理完了: {recording_info['username']}")
            
        except Exception as e:
            self.logger.error(f"後処理エラー: {recording_info['username']} - {e}")
    
    async def _convert_recording(self, recording_info: Dict[str, Any]):
        """録画ファイル変換"""
        # FFmpegによる変換（将来実装）
        pass
    
    async def stop_recording(self, url: str) -> bool:
        """録画停止"""
        try:
            if not self.is_recording(url):
                self.logger.warning(f"録画停止: 対象なし - {url}")
                return False
            
            recording_info = self.active_recordings[url]
            username = recording_info['username']
            process = recording_info.get('process')
            
            if process:
                # プロセス終了
                recording_info['status'] = RecordingStatus.STOPPING.value
                
                if platform.system() == "Windows":
                    process.terminate()
                else:
                    process.send_signal(signal.SIGTERM)
                
                # 強制終了の猶予
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                
                self.logger.info(f"✅ 録画停止: {username}")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"録画停止エラー: {url} - {e}")
            return False
    
    def is_recording(self, url: str) -> bool:
        """録画中確認"""
        return url in self.active_recordings
    
    def get_active_recordings(self) -> Dict[str, Any]:
        """アクティブな録画一覧取得"""
        with self._lock:
            return {url: info.copy() for url, info in self.active_recordings.items()}
    
    def get_statistics(self) -> Dict[str, Any]:
        """統計情報取得"""
        total_recordings = len(self.completed_recordings) + len(self.failed_recordings)
        success_rate = 0.0
        
        if total_recordings > 0:
            success_rate = (len(self.completed_recordings) / total_recordings) * 100
        
        total_size = sum(rec.get('file_size', 0) for rec in self.completed_recordings)
        
        return {
            'total_recordings': total_recordings,
            'completed_recordings': len(self.completed_recordings),
            'failed_recordings': len(self.failed_recordings),
            'active_recordings': len(self.active_recordings),
            'success_rate': round(success_rate, 1),
            'total_file_size_mb': round(total_size / (1024 * 1024), 1)
        }
    
    def _extract_username(self, url: str) -> str:
        """URLからユーザー名抽出"""
        try:
            return url.rstrip('/').split('/')[-1]
        except:
            return "unknown"
    
    def _generate_filename(self, username: str) -> str:
        """ファイル名生成"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # テンプレート適用
        template = self.recording_config.filename_template
        filename = template.format(
            user=username,
            date=datetime.now().strftime('%Y%m%d'),
            time=datetime.now().strftime('%H%M%S'),
            title=username  # 実際のタイトル取得は将来実装
        )
        
        return f"{filename}.mp4"
    
    def _format_file_size(self, size_bytes: int) -> str:
        """ファイルサイズフォーマット"""
        if size_bytes == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        
        return f"{size_bytes:.1f} TB"
    
    def shutdown(self):
        """シャットダウン"""
        self.logger.info("録画エンジンシャットダウン開始")
        self.shutdown_requested = True
        
        # 全ての録画を停止
        urls_to_stop = list(self.active_recordings.keys())
        for url in urls_to_stop:
            asyncio.create_task(self.stop_recording(url))
        
        self.logger.info("録画エンジンシャットダウン完了")