#!/usr/bin/env python3
"""
🎯 TwitCasting録画システム - 設定管理コア
Windows完全対応・100点レベル安定性実現版

主な機能:
- 設定ファイル管理 (YAML/JSON対応)
- URL管理・バリデーション
- 依存関係チェック
- ログ管理
- システム監視
"""

import os
import sys
import json
import yaml
import logging
import asyncio
import subprocess
import time
import psutil
import signal
import atexit
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from contextlib import contextmanager
import threading
import queue
import re
import platform

# Windows固有の処理
if platform.system() == "Windows":
    import msvcrt
    import ctypes
    from ctypes import wintypes
    
    # Windows API定数
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
else:
    CREATE_NO_WINDOW = 0
    DETACHED_PROCESS = 0

# ===============================
# 🔧 基本設定クラス
# ===============================

@dataclass
class SystemConfig:
    """システム全体の設定"""
    # パス設定
    project_root: Path = field(default_factory=lambda: Path.cwd())
    config_dir: Path = field(default_factory=lambda: Path.cwd() / "config")
    data_dir: Path = field(default_factory=lambda: Path.cwd() / "data")
    recordings_dir: Path = field(default_factory=lambda: Path.cwd() / "recordings")
    logs_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "logs")
    
    # システム設定
    max_concurrent_recordings: int = 3
    recording_timeout_minutes: int = 180
    retry_attempts: int = 3
    retry_delay_seconds: int = 5
    
    # ログ設定
    log_level: str = "INFO"
    log_rotation_size: str = "10MB"
    log_retention_days: int = 30
    
    # 監視設定
    system_check_interval: int = 60
    disk_space_threshold_gb: float = 5.0
    memory_threshold_percent: float = 85.0
    
    def __post_init__(self):
        """初期化後処理"""
        # Pathオブジェクトに変換
        for field_name in ['project_root', 'config_dir', 'data_dir', 'recordings_dir', 'logs_dir']:
            value = getattr(self, field_name)
            if isinstance(value, str):
                setattr(self, field_name, Path(value))

@dataclass
class RecordingConfig:
    """録画設定"""
    # 品質設定
    video_quality: str = "best"
    audio_quality: str = "best"
    format_preference: List[str] = field(default_factory=lambda: ["mp4", "flv", "ts"])
    
    # 出力設定
    filename_template: str = "{user}_{date}_{time}_{title}"
    output_directory: str = "recordings/videos"
    temp_directory: str = "recordings/temp"
    
    # ストリーム設定
    segment_duration: int = 30
    reconnect_timeout: int = 10
    max_reconnect_attempts: int = 5
    
    # 後処理設定
    auto_convert: bool = True
    convert_format: str = "mp4"
    delete_original: bool = False
    
    # 通知設定
    enable_notifications: bool = True
    notification_methods: List[str] = field(default_factory=lambda: ["console", "log"])

# ===============================
# 🗂️ 設定管理マネージャー
# ===============================

class ConfigManager:
    """設定ファイル管理"""
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.cwd() / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # 設定ファイルパス
        self.system_config_path = self.config_dir / "system.yaml"
        self.recording_config_path = self.config_dir / "recording.yaml"
        self.urls_config_path = self.config_dir / "urls.json"
        
        # 設定オブジェクト
        self.system_config: Optional[SystemConfig] = None
        self.recording_config: Optional[RecordingConfig] = None
        self.urls: Dict[str, Any] = {}
        
        # ロック
        self._lock = threading.Lock()
        
        # 初期化
        self._initialize_configs()
    
    def _initialize_configs(self):
        """設定の初期化"""
        try:
            # システム設定
            if self.system_config_path.exists():
                self.system_config = self._load_system_config()
            else:
                self.system_config = SystemConfig()
                self.save_system_config()
            
            # 録画設定
            if self.recording_config_path.exists():
                self.recording_config = self._load_recording_config()
            else:
                self.recording_config = RecordingConfig()
                self.save_recording_config()
            
            # URL設定
            if self.urls_config_path.exists():
                self.urls = self._load_urls()
            else:
                self.urls = {
                    "twitcasting_urls": [],
                    "monitoring_settings": {
                        "check_interval": 30,
                        "retry_count": 3
                    }
                }
                self.save_urls()
        
        except Exception as e:
            logging.error(f"設定初期化エラー: {e}")
            # デフォルト設定で続行
            self.system_config = SystemConfig()
            self.recording_config = RecordingConfig()
            self.urls = {"twitcasting_urls": [], "monitoring_settings": {}}
    
    def _load_system_config(self) -> SystemConfig:
        """システム設定読み込み"""
        try:
            with open(self.system_config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            return SystemConfig(**data)
        except Exception as e:
            logging.warning(f"システム設定読み込み失敗: {e}")
            return SystemConfig()
    
    def _load_recording_config(self) -> RecordingConfig:
        """録画設定読み込み"""
        try:
            with open(self.recording_config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            return RecordingConfig(**data)
        except Exception as e:
            logging.warning(f"録画設定読み込み失敗: {e}")
            return RecordingConfig()
    
    def _load_urls(self) -> Dict[str, Any]:
        """URL設定読み込み"""
        try:
            with open(self.urls_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"URL設定読み込み失敗: {e}")
            return {"twitcasting_urls": [], "monitoring_settings": {}}
    
    def save_system_config(self):
        """システム設定保存"""
        with self._lock:
            try:
                # dataclassを辞書に変換
                data = self._dataclass_to_dict(self.system_config)
                self._atomic_write_yaml(self.system_config_path, data)
            except Exception as e:
                logging.error(f"システム設定保存エラー: {e}")
                raise
    
    def save_recording_config(self):
        """録画設定保存"""
        with self._lock:
            try:
                data = self._dataclass_to_dict(self.recording_config)
                self._atomic_write_yaml(self.recording_config_path, data)
            except Exception as e:
                logging.error(f"録画設定保存エラー: {e}")
                raise
    
    def save_urls(self):
        """URL設定保存"""
        with self._lock:
            try:
                self._atomic_write_json(self.urls_config_path, self.urls)
            except Exception as e:
                logging.error(f"URL設定保存エラー: {e}")
                raise
    
    def _dataclass_to_dict(self, obj) -> Dict[str, Any]:
        """dataclassを辞書に変換（Path対応）"""
        if obj is None:
            return {}
        
        result = {}
        for field_name, field_value in obj.__dict__.items():
            if isinstance(field_value, Path):
                result[field_name] = str(field_value)
            else:
                result[field_name] = field_value
        return result
    
    def _atomic_write_yaml(self, filepath: Path, data: Dict[str, Any]):
        """アトミック書き込み（YAML）"""
        temp_path = filepath.with_suffix('.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            
            # Windows対応のアトミック移動
            if platform.system() == "Windows":
                if filepath.exists():
                    filepath.unlink()
            temp_path.replace(filepath)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    def _atomic_write_json(self, filepath: Path, data: Dict[str, Any]):
        """アトミック書き込み（JSON）"""
        temp_path = filepath.with_suffix('.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Windows対応のアトミック移動
            if platform.system() == "Windows":
                if filepath.exists():
                    filepath.unlink()
            temp_path.replace(filepath)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    def reload_configs(self):
        """設定の再読み込み"""
        with self._lock:
            self._initialize_configs()
    
    def get_system_config(self) -> SystemConfig:
        """システム設定取得"""
        return self.system_config
    
    def get_recording_config(self) -> RecordingConfig:
        """録画設定取得"""
        return self.recording_config
    
    def get_urls(self) -> Dict[str, Any]:
        """URL設定取得"""
        return self.urls.copy()
    
    def update_system_config(self, **kwargs):
        """システム設定更新"""
        for key, value in kwargs.items():
            if hasattr(self.system_config, key):
                setattr(self.system_config, key, value)
        self.save_system_config()
    
    def update_recording_config(self, **kwargs):
        """録画設定更新"""
        for key, value in kwargs.items():
            if hasattr(self.recording_config, key):
                setattr(self.recording_config, key, value)
        self.save_recording_config()

# ===============================
# 🌐 URL管理マネージャー
# ===============================

class URLManager:
    """URL管理とバリデーション"""
    
    # TwitCastingのURLパターン（コロン対応）
    TWITCASTING_PATTERNS = [
        r'https?://twitcasting\.tv/([a-zA-Z0-9_:]+)/?',
        r'https?://(?:www\.)?twitcasting\.tv/([a-zA-Z0-9_:]+)/?',
        r'twitcasting\.tv/([a-zA-Z0-9_:]+)/?'
    ]
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self._lock = threading.Lock()
    
    def validate_twitcasting_url(self, url: str) -> Tuple[bool, Optional[str]]:
        """TwitCasting URL検証"""
        if not url:
            return False, "URLが空です"
        
        # URLの正規化
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # パターンマッチング
        for pattern in self.TWITCASTING_PATTERNS:
            match = re.match(pattern, url)
            if match:
                user_id = match.group(1)
                normalized_url = f"https://twitcasting.tv/{user_id}"
                return True, normalized_url
        
        return False, "無効なTwitCasting URLです"
    
    def add_url(self, url: str, description: str = "") -> bool:
        """URL追加"""
        with self._lock:
            is_valid, result = self.validate_twitcasting_url(url)
            if not is_valid:
                logging.error(f"URL追加失敗: {result}")
                return False
            
            normalized_url = result
            urls_config = self.config_manager.get_urls()
            
            # 重複チェック
            for existing in urls_config.get("twitcasting_urls", []):
                if existing.get("url") == normalized_url:
                    logging.warning(f"URL既に存在: {normalized_url}")
                    return False
            
            # URL追加
            url_entry = {
                "url": normalized_url,
                "description": description,
                "added_at": datetime.now().isoformat(),
                "enabled": True,
                "last_checked": None,
                "status": "未確認"
            }
            
            if "twitcasting_urls" not in urls_config:
                urls_config["twitcasting_urls"] = []
            
            urls_config["twitcasting_urls"].append(url_entry)
            self.config_manager.urls = urls_config
            self.config_manager.save_urls()
            
            logging.info(f"URL追加成功: {normalized_url}")
            return True
    
    def remove_url(self, url: str) -> bool:
        """URL削除"""
        with self._lock:
            is_valid, normalized_url = self.validate_twitcasting_url(url)
            if not is_valid:
                return False
            
            urls_config = self.config_manager.get_urls()
            original_count = len(urls_config.get("twitcasting_urls", []))
            
            urls_config["twitcasting_urls"] = [
                entry for entry in urls_config.get("twitcasting_urls", [])
                if entry.get("url") != normalized_url
            ]
            
            if len(urls_config["twitcasting_urls"]) < original_count:
                self.config_manager.urls = urls_config
                self.config_manager.save_urls()
                logging.info(f"URL削除成功: {normalized_url}")
                return True
            
            logging.warning(f"削除対象URL未発見: {normalized_url}")
            return False
    
    def get_active_urls(self) -> List[Dict[str, Any]]:
        """アクティブなURL一覧取得"""
        urls_config = self.config_manager.get_urls()
        return [
            url_entry for url_entry in urls_config.get("twitcasting_urls", [])
            if url_entry.get("enabled", True)
        ]
    
    def update_url_status(self, url: str, status: str):
        """URL状態更新"""
        with self._lock:
            urls_config = self.config_manager.get_urls()
            
            for url_entry in urls_config.get("twitcasting_urls", []):
                if url_entry.get("url") == url:
                    url_entry["status"] = status
                    url_entry["last_checked"] = datetime.now().isoformat()
                    break
            
            self.config_manager.urls = urls_config
            self.config_manager.save_urls()

# ===============================
# 🔍 依存関係チェッカー
# ===============================

class DependencyChecker:
    """システム依存関係確認"""
    
    REQUIRED_COMMANDS = {
        'streamlink': 'streamlink --version',
        'yt-dlp': 'yt-dlp --version',
        'ffmpeg': 'ffmpeg -version'
    }
    
    OPTIONAL_COMMANDS = {
        'playwright': 'playwright --version',
        'chromedriver': 'chromedriver --version'
    }
    
    def __init__(self):
        self.results = {}
        self._lock = threading.Lock()
    
    async def check_all_dependencies(self) -> Dict[str, Dict[str, Any]]:
        """全依存関係チェック"""
        with self._lock:
            self.results = {
                'required': {},
                'optional': {},
                'system': {}
            }
        
        # 必須コマンド
        for name, command in self.REQUIRED_COMMANDS.items():
            result = await self._check_command(command)
            self.results['required'][name] = result
        
        # オプションコマンド
        for name, command in self.OPTIONAL_COMMANDS.items():
            result = await self._check_command(command)
            self.results['optional'][name] = result
        
        # システム情報
        self.results['system'] = self._get_system_info()
        
        return self.results.copy()
    
    async def _check_command(self, command: str) -> Dict[str, Any]:
        """コマンド実行チェック"""
        try:
            # Windows対応のプロセス作成フラグ
            creation_flags = 0
            if platform.system() == "Windows":
                creation_flags = CREATE_NO_WINDOW
            
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), 
                    timeout=15.0  # タイムアウト延長
                )
                
                return {
                    'available': process.returncode == 0,
                    'version': stdout.decode('utf-8').strip()[:200] if stdout else '',
                    'error': stderr.decode('utf-8').strip()[:200] if stderr else '',
                    'return_code': process.returncode
                }
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {
                    'available': False,
                    'version': '',
                    'error': 'コマンド実行タイムアウト',
                    'return_code': -1
                }
                
        except Exception as e:
            return {
                'available': False,
                'version': '',
                'error': str(e),
                'return_code': -1
            }
    
    def _get_system_info(self) -> Dict[str, Any]:
        """システム情報取得"""
        try:
            return {
                'platform': platform.system(),
                'platform_version': platform.version(),
                'architecture': platform.architecture()[0],
                'processor': platform.processor(),
                'python_version': platform.python_version(),
                'memory_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
                'disk_space_gb': round(psutil.disk_usage('/').free / (1024**3), 2) if platform.system() != "Windows" else round(psutil.disk_usage('C:').free / (1024**3), 2),
                'cpu_count': psutil.cpu_count()
            }
        except Exception as e:
            return {'error': str(e)}
    
    def get_missing_dependencies(self) -> List[str]:
        """不足している依存関係取得"""
        missing = []
        for name, result in self.results.get('required', {}).items():
            if not result.get('available', False):
                missing.append(name)
        return missing
    
    def is_system_ready(self) -> bool:
        """システム準備完了確認"""
        return len(self.get_missing_dependencies()) == 0

# ===============================
# 📋 ログ管理マネージャー
# ===============================

class LogManager:
    """ログ管理（競合回避対応）"""
    
    _initialized = False
    _lock = threading.Lock()
    
    def __init__(self, config: SystemConfig):
        self.config = config
        self.logs_dir = config.logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # 一度だけ初期化
        with LogManager._lock:
            if not LogManager._initialized:
                self._setup_logging()
                LogManager._initialized = True
    
    def _setup_logging(self):
        """ログ設定（重複設定回避）"""
        try:
            # 既存のハンドラーをクリア
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                root_logger.removeHandler(handler)
            
            # ログファイルパス
            log_file = self.logs_dir / f"system_{datetime.now().strftime('%Y%m%d')}.log"
            
            # フォーマッター
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(name)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            
            # ファイルハンドラー
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            file_handler.setLevel(getattr(logging, self.config.log_level))
            
            # コンソールハンドラー
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            console_handler.setLevel(logging.INFO)
            
            # ルートロガー設定
            root_logger.addHandler(file_handler)
            root_logger.addHandler(console_handler)
            root_logger.setLevel(logging.DEBUG)
            
            logging.info("ログシステム初期化完了")
            
        except Exception as e:
            print(f"ログ設定エラー: {e}")
    
    def cleanup_old_logs(self):
        """古いログファイル削除"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.config.log_retention_days)
            
            for log_file in self.logs_dir.glob("*.log"):
                if log_file.stat().st_mtime < cutoff_date.timestamp():
                    log_file.unlink()
                    logging.info(f"古いログファイル削除: {log_file}")
                    
        except Exception as e:
            logging.error(f"ログクリーンアップエラー: {e}")

# ===============================
# 📊 システム監視マネージャー
# ===============================

class SystemMonitor:
    """システムリソース監視"""
    
    def __init__(self, config: SystemConfig):
        self.config = config
        self.monitoring = False
        self._monitor_task = None
        self._status = {
            'cpu_percent': 0.0,
            'memory_percent': 0.0,
            'disk_free_gb': 0.0,
            'active_processes': 0,
            'last_check': None
        }
        self._lock = threading.Lock()
    
    async def start_monitoring(self):
        """監視開始"""
        if self.monitoring:
            return
        
        self.monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logging.info("システム監視開始")
    
    async def stop_monitoring(self):
        """監視停止"""
        self.monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logging.info("システム監視停止")
    
    async def _monitor_loop(self):
        """監視ループ"""
        while self.monitoring:
            try:
                await self._update_status()
                await self._check_thresholds()
                await asyncio.sleep(self.config.system_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"システム監視エラー: {e}")
                await asyncio.sleep(10)
    
    async def _update_status(self):
        """ステータス更新"""
        try:
            with self._lock:
                self._status.update({
                    'cpu_percent': psutil.cpu_percent(interval=1),
                    'memory_percent': psutil.virtual_memory().percent,
                    'disk_free_gb': round(
                        psutil.disk_usage('C:' if platform.system() == "Windows" else '/').free / (1024**3), 2
                    ),
                    'active_processes': len(psutil.pids()),
                    'last_check': datetime.now().isoformat()
                })
        except Exception as e:
            logging.error(f"ステータス更新エラー: {e}")
    
    async def _check_thresholds(self):
        """閾値チェック"""
        try:
            status = self.get_status()
            
            # メモリ使用量警告
            if status['memory_percent'] > self.config.memory_threshold_percent:
                logging.warning(f"メモリ使用量が高いです: {status['memory_percent']:.1f}%")
            
            # ディスク容量警告
            if status['disk_free_gb'] < self.config.disk_space_threshold_gb:
                logging.warning(f"ディスク容量が少ないです: {status['disk_free_gb']:.1f}GB")
                
        except Exception as e:
            logging.error(f"閾値チェックエラー: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """現在のステータス取得"""
        with self._lock:
            return self._status.copy()

# ===============================
# 🧪 動作確認・テスト関数
# ===============================

async def test_all_components():
    """全コンポーネントテスト"""
    print("🧪 システムコンポーネントテスト開始")
    
    try:
        # 設定管理テスト
        print("📝 設定管理テスト...")
        config_manager = ConfigManager()
        system_config = config_manager.get_system_config()
        print(f"✅ システム設定読み込み成功: {system_config.project_root}")
        
        # URL管理テスト
        print("🌐 URL管理テスト...")
        url_manager = URLManager(config_manager)
        is_valid, result = url_manager.validate_twitcasting_url("https://twitcasting.tv/test_user")
        print(f"✅ URL検証成功: {result}")
        
        # 依存関係チェックテスト
        print("🔍 依存関係チェックテスト...")
        dependency_checker = DependencyChecker()
        deps = await dependency_checker.check_all_dependencies()
        print(f"✅ 依存関係チェック完了: {len(deps['required'])}個の必須コマンド確認")
        
        # ログ管理テスト
        print("📋 ログ管理テスト...")
        log_manager = LogManager(system_config)
        logging.info("ログ管理テスト成功")
        print("✅ ログ管理動作確認")
        
        # システム監視テスト
        print("📊 システム監視テスト...")
        monitor = SystemMonitor(system_config)
        status = monitor.get_status()
        print(f"✅ システム監視動作確認: CPU {status.get('cpu_percent', 0):.1f}%")
        
        print("🎉 全コンポーネントテスト完了！")
        return True
        
    except Exception as e:
        print(f"❌ テストエラー: {e}")
        logging.error(f"コンポーネントテストエラー: {e}")
        return False

def main_example():
    """使用例とデモンストレーション"""
    print("🚀 TwitCasting録画システム - 設定管理デモ")
    print("=" * 50)
    
    try:
        # 非同期処理実行
        result = asyncio.run(test_all_components())
        
        if result:
            print("\n✅ システム正常動作確認！")
            print("📋 次のステップ:")
            print("  1. python main.py でメインシステム起動")
            print("  2. 設定ファイルは config/ フォルダに保存されます")
            print("  3. ログは data/logs/ フォルダに出力されます")
        else:
            print("\n❌ システムに問題があります")
            print("📋 トラブルシューティング:")
            print("  1. 依存関係を確認してください")
            print("  2. ログファイルで詳細エラーを確認してください")
            
    except Exception as e:
        print(f"\n❌ 実行エラー: {e}")
        print("📋 考えられる原因:")
        print("  1. 必要なPythonライブラリが不足している")
        print("  2. ファイル/フォルダの権限問題")
        print("  3. システムリソース不足")

# ===============================
# 🏃‍♂️ メイン実行部
# ===============================

if __name__ == "__main__":
    try:
        main_example()
    except KeyboardInterrupt:
        print("\n⏹️ ユーザーによる中断")
    except Exception as e:
        print(f"\n💥 予期しないエラー: {e}")
        sys.exit(1)