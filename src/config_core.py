#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_core.py - 設定管理コア（完全対応修正版）
実際のYAMLファイルに完全対応・100点レベル安定性実現
"""

import os
import sys
import json
import yaml
import logging
import asyncio
import subprocess
import platform
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, field, fields

# Windows固有の処理
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0

# ===============================
# 🔧 完全対応設定クラス
# ===============================

@dataclass
class SystemConfig:
    """システム全体の設定（実YAMLファイル完全対応版）"""
    # 基本ディレクトリ設定
    project_root: Path = field(default_factory=lambda: Path.cwd())
    config_dir: Path = field(default_factory=lambda: Path.cwd() / "config")
    data_dir: Path = field(default_factory=lambda: Path.cwd() / "data")
    recordings_dir: Path = field(default_factory=lambda: Path.cwd() / "recordings")
    logs_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "logs")
    
    # 並行処理・性能設定
    max_concurrent_recordings: int = 3
    recording_timeout_minutes: int = 180
    retry_attempts: int = 3
    retry_delay_seconds: int = 5
    
    # システム監視設定
    system_check_interval: int = 60
    disk_space_threshold_gb: float = 5.0
    memory_threshold_percent: float = 85.0
    
    # ログ設定
    log_level: str = "INFO"
    log_rotation_size: str = "10MB"  # 実YAMLに合わせて文字列型
    log_retention_days: int = 30
    
    def __post_init__(self):
        """初期化後処理（型変換・バリデーション）"""
        # Path型変換
        if isinstance(self.project_root, str):
            self.project_root = Path(self.project_root)
        if isinstance(self.config_dir, str):
            self.config_dir = Path(self.config_dir)
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        if isinstance(self.recordings_dir, str):
            self.recordings_dir = Path(self.recordings_dir)
        if isinstance(self.logs_dir, str):
            self.logs_dir = Path(self.logs_dir)
        
        # バリデーション
        if self.max_concurrent_recordings < 1:
            self.max_concurrent_recordings = 1
        if self.recording_timeout_minutes < 1:
            self.recording_timeout_minutes = 60
        if self.disk_space_threshold_gb < 0.1:
            self.disk_space_threshold_gb = 1.0
        if self.memory_threshold_percent < 10 or self.memory_threshold_percent > 95:
            self.memory_threshold_percent = 85.0

@dataclass
class RecordingConfig:
    """録画設定（実YAMLファイル完全対応版）"""
    # 品質設定
    video_quality: str = "best"
    audio_quality: str = "best"
    
    # ファイル管理設定
    filename_template: str = "{user}_{date}_{time}_{title}"
    output_directory: str = "recordings/videos"
    temp_directory: str = "recordings/temp"
    
    # 変換設定
    auto_convert: bool = True
    convert_format: str = "mp4"
    delete_original: bool = False
    format_preference: List[str] = field(default_factory=lambda: ["mp4", "flv", "ts"])
    
    # 接続・再試行設定
    max_reconnect_attempts: int = 5
    reconnect_timeout: int = 10
    segment_duration: int = 30
    
    # 通知設定
    enable_notifications: bool = True
    notification_methods: List[str] = field(default_factory=lambda: ["console", "log"])
    
    def __post_init__(self):
        """初期化後処理（バリデーション）"""
        # 品質設定の正規化
        valid_qualities = ["best", "worst", "hd", "medium", "low"]
        if self.video_quality not in valid_qualities:
            self.video_quality = "best"
        if self.audio_quality not in valid_qualities:
            self.audio_quality = "best"
        
        # 数値バリデーション
        if self.max_reconnect_attempts < 0:
            self.max_reconnect_attempts = 3
        if self.reconnect_timeout < 1:
            self.reconnect_timeout = 10
        if self.segment_duration < 5:
            self.segment_duration = 30
        
        # フォーマット設定バリデーション
        valid_formats = ["mp4", "flv", "ts", "mkv", "avi"]
        if self.convert_format not in valid_formats:
            self.convert_format = "mp4"
        
        # format_preference の重複排除・有効性チェック
        self.format_preference = [f for f in self.format_preference if f in valid_formats]
        if not self.format_preference:
            self.format_preference = ["mp4"]
        
        # notification_methods の有効性チェック
        valid_methods = ["console", "log", "email", "discord", "slack"]
        self.notification_methods = [m for m in self.notification_methods if m in valid_methods]
        if not self.notification_methods:
            self.notification_methods = ["console", "log"]

# ===============================
# 🗂️ 完全対応設定管理マネージャー
# ===============================

class ConfigManager:
    """設定ファイル管理（完全対応修正版）"""
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.cwd() / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        self.system_config_path = self.config_dir / "system.yaml"
        self.recording_config_path = self.config_dir / "recording.yaml"
        self.urls_config_path = self.config_dir / "urls.json"
        
        self.system_config: Optional[SystemConfig] = None
        self.recording_config: Optional[RecordingConfig] = None
        self.urls: Dict[str, Any] = {}
        
        # 初期化時に自動読み込み
        self._initialize_configs()
    
    def _initialize_configs(self):
        """設定の初期化"""
        self.system_config = self._load_config(self.system_config_path, SystemConfig)
        self.recording_config = self._load_config(self.recording_config_path, RecordingConfig)
        self.urls = self._load_urls()

    def _load_config(self, path: Path, config_class):
        """設定ファイル読み込み（完全対応版）"""
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                
                # 未知のキーワード引数を除外
                valid_fields = {f.name for f in fields(config_class)}
                filtered_data = {k: v for k, v in data.items() if k in valid_fields}
                
                # 除外されたキーがある場合はログ出力
                excluded_keys = set(data.keys()) - valid_fields
                if excluded_keys:
                    logging.info(f"{path.name}: 未対応キー除外 - {excluded_keys}")
                
                return config_class(**filtered_data)
                
            except Exception as e:
                logging.warning(f"{path.name} 読み込み失敗: {e}")
                return config_class()
        else:
            return config_class()

    def _load_urls(self) -> Dict[str, Any]:
        """URL設定読み込み"""
        if self.urls_config_path.exists():
            try:
                with open(self.urls_config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {"twitcasting_urls": []}
            except Exception as e:
                logging.warning(f"URL設定読み込み失敗: {e}")
        return {"twitcasting_urls": []}

    # ✅ 修正1: 不足していたload_config()メソッド追加
    async def load_config(self):
        """設定再読み込み（main.pyからの呼び出し対応）"""
        try:
            logging.info("設定ファイル再読み込み開始")
            self._initialize_configs()
            logging.info("✅ 設定ファイル再読み込み完了")
        except Exception as e:
            logging.error(f"設定再読み込みエラー: {e}")
            raise

    def save_system_config(self):
        """システム設定保存"""
        self._save_config(self.system_config_path, self.system_config)

    def save_recording_config(self):
        """録画設定保存"""
        self._save_config(self.recording_config_path, self.recording_config)

    def save_urls(self):
        """URL設定保存"""
        self._atomic_write_json(self.urls_config_path, self.urls)

    def save_all_configs(self):
        """全設定保存"""
        self.save_system_config()
        self.save_recording_config()
        self.save_urls()

    def _save_config(self, path: Path, config_obj):
        """設定オブジェクト保存"""
        try:
            data = self._dataclass_to_dict(config_obj)
            self._atomic_write_yaml(path, data)
            logging.debug(f"{path.name} 保存完了")
        except Exception as e:
            logging.error(f"{path.name} 保存エラー: {e}")

    def _dataclass_to_dict(self, obj) -> Dict[str, Any]:
        """dataclassを辞書に変換"""
        if obj is None: 
            return {}
        
        result = {}
        # ✅ 修正2: field(obj) → fields(obj) に修正
        for f in fields(obj):
            value = getattr(obj, f.name)
            # Path型は文字列に変換
            if isinstance(value, Path):
                result[f.name] = str(value)
            else:
                result[f.name] = value
        
        return result

    def _atomic_write_yaml(self, filepath: Path, data: Dict[str, Any]):
        """YAML原子的書き込み"""
        temp_path = filepath.with_suffix('.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=True)
            temp_path.replace(filepath)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise e

    def _atomic_write_json(self, filepath: Path, data: Dict[str, Any]):
        """JSON原子的書き込み"""
        temp_path = filepath.with_suffix('.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
            temp_path.replace(filepath)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise e

    def get_system_config(self) -> SystemConfig:
        """システム設定取得"""
        return self.system_config

    def get_recording_config(self) -> RecordingConfig:
        """録画設定取得"""
        return self.recording_config

    def get_urls(self) -> Dict[str, Any]:
        """URL設定取得"""
        return self.urls.copy()
    
    def config_file_exists(self) -> bool:
        """設定ファイル存在チェック"""
        return (self.system_config_path.exists() and 
                self.recording_config_path.exists() and 
                self.urls_config_path.exists())

    async def create_default_config(self):
        """デフォルト設定ファイル作成"""
        try:
            if not self.system_config_path.exists(): 
                self.save_system_config()
                logging.info(f"デフォルトシステム設定作成: {self.system_config_path}")
            
            if not self.recording_config_path.exists(): 
                self.save_recording_config()
                logging.info(f"デフォルト録画設定作成: {self.recording_config_path}")
            
            if not self.urls_config_path.exists(): 
                self.save_urls()
                logging.info(f"デフォルトURL設定作成: {self.urls_config_path}")
                
        except Exception as e:
            logging.error(f"デフォルト設定作成エラー: {e}")
            raise

    async def validate_config(self) -> Dict[str, Any]:
        """設定検証"""
        issues = []
        
        try:
            # システム設定検証
            if self.system_config:
                if not self.system_config.recordings_dir.parent.exists():
                    issues.append(f"録画ディレクトリの親が存在しません: {self.system_config.recordings_dir.parent}")
                
                if self.system_config.max_concurrent_recordings < 1 or self.system_config.max_concurrent_recordings > 10:
                    issues.append(f"同時録画数が範囲外です: {self.system_config.max_concurrent_recordings} (1-10)")
            
            # 録画設定検証
            if self.recording_config:
                if not self.recording_config.format_preference:
                    issues.append("format_preference が空です")
                
                if not self.recording_config.notification_methods:
                    issues.append("notification_methods が空です")
            
            return {
                'valid': len(issues) == 0,
                'issues': issues
            }
            
        except Exception as e:
            return {
                'valid': False,
                'issues': [f"設定検証中にエラー: {e}"]
            }

    async def auto_repair_config(self) -> bool:
        """設定自動修復"""
        try:
            repaired = False
            
            # システム設定修復
            if self.system_config:
                # ディレクトリ作成
                for dir_path in [self.system_config.recordings_dir, 
                               self.system_config.data_dir, 
                               self.system_config.logs_dir]:
                    if not dir_path.exists():
                        dir_path.mkdir(parents=True, exist_ok=True)
                        repaired = True
                        logging.info(f"ディレクトリ作成: {dir_path}")
            
            # 録画設定修復
            if self.recording_config:
                if not self.recording_config.format_preference:
                    self.recording_config.format_preference = ["mp4"]
                    repaired = True
                
                if not self.recording_config.notification_methods:
                    self.recording_config.notification_methods = ["console", "log"]
                    repaired = True
            
            if repaired:
                self.save_all_configs()
                logging.info("✅ 設定自動修復完了")
            
            return True
            
        except Exception as e:
            logging.error(f"設定自動修復エラー: {e}")
            return False

    def update_system_config(self, **kwargs):
        """システム設定更新"""
        if self.system_config:
            for key, value in kwargs.items():
                if hasattr(self.system_config, key):
                    setattr(self.system_config, key, value)
            self.save_system_config()

    def update_recording_config(self, **kwargs):
        """録画設定更新"""
        if self.recording_config:
            for key, value in kwargs.items():
                if hasattr(self.recording_config, key):
                    setattr(self.recording_config, key, value)
            self.save_recording_config()

# ===============================
# 🔍 依存関係チェッカー（変更なし）
# ===============================

class DependencyChecker:
    """システム依存関係確認"""
    
    REQUIRED_COMMANDS = {
        'streamlink': 'streamlink --version', 
        'yt-dlp': 'yt-dlp --version', 
        'ffmpeg': 'ffmpeg -version'
    }
    OPTIONAL_COMMANDS = {
        'playwright': 'playwright --version'
    }
    
    async def check_all_dependencies(self) -> Dict[str, Dict[str, Any]]:
        """全依存関係チェック"""
        results = {'required': {}, 'optional': {}}
        
        for name, command in self.REQUIRED_COMMANDS.items():
            results['required'][name] = await self._check_command(command)
        
        for name, command in self.OPTIONAL_COMMANDS.items():
            results['optional'][name] = await self._check_command(command)
        
        return results
    
    async def _check_command(self, command: str) -> Dict[str, Any]:
        """コマンド実行チェック"""
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10.0)
            
            return {
                'available': process.returncode == 0,
                'version': stdout.decode('utf-8', errors='ignore').strip().split('\n')[0],
                'error': stderr.decode('utf-8', errors='ignore').strip()
            }
        except Exception as e:
            return {'available': False, 'error': str(e)}

# ===============================
# 🧪 テスト・デバッグ用関数
# ===============================

async def test_config_system():
    """設定システムテスト"""
    print("🧪 設定システムテスト開始")
    
    try:
        # ConfigManager初期化
        config_manager = ConfigManager()
        
        # 設定読み込みテスト
        await config_manager.load_config()
        print("✅ 設定読み込み成功")
        
        # 設定検証テスト
        validation = await config_manager.validate_config()
        print(f"📋 設定検証: {'✅ 正常' if validation['valid'] else '❌ 問題あり'}")
        if validation['issues']:
            for issue in validation['issues']:
                print(f"  - {issue}")
        
        # 自動修復テスト
        repair_result = await config_manager.auto_repair_config()
        print(f"🔧 自動修復: {'✅ 成功' if repair_result else '❌ 失敗'}")
        
        # 設定値表示
        sys_config = config_manager.get_system_config()
        rec_config = config_manager.get_recording_config()
        
        print(f"📊 システム設定:")
        print(f"  - 最大同時録画数: {sys_config.max_concurrent_recordings}")
        print(f"  - 録画ディレクトリ: {sys_config.recordings_dir}")
        print(f"  - ログレベル: {sys_config.log_level}")
        
        print(f"📊 録画設定:")
        print(f"  - 映像品質: {rec_config.video_quality}")
        print(f"  - 音声品質: {rec_config.audio_quality}")
        print(f"  - 対応フォーマット: {rec_config.format_preference}")
        
        print("🎉 設定システムテスト完了")
        
    except Exception as e:
        print(f"❌ テストエラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # テスト実行
    asyncio.run(test_config_system())