#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - ラクロク TwitCasting 統合実行ファイル
限定配信対応 TwitCasting 自動録画システム
"""

import asyncio
import argparse
import logging
import sys
import os
import time
import signal
import threading
from pathlib import Path
from datetime import datetime

# 環境変数読み込み
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ python-dotenv未インストール: pip install python-dotenv")

# srcディレクトリをPythonパスに追加
src_dir = Path(__file__).parent / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

# 自作モジュールインポート
try:
    from config_core import ConfigManager, URLManager, DependencyChecker, LogManager, SystemMonitor
    # その他のモジュールは後でインポート（循環インポート回避）
except ImportError as e:
    print(f"❌ モジュールインポートエラー: {e}")
    print("srcフォルダに必要なファイルが配置されているか確認してください")
    sys.exit(1)

class RakurekoMain:
    """ラクロク メインアプリケーション"""
    
    def __init__(self, args):
        self.args = args
        self.base_dir = Path(args.config_dir) if args.config_dir else Path.cwd()
        
        # 設定管理初期化（最初に実行）
        self.config_manager = ConfigManager()
        self.system_config = self.config_manager.get_system_config()
        
        # ログ初期化（SystemConfigを渡す）
        self.log_manager = LogManager(self.system_config)
        self.logger = logging.getLogger(__name__)
        
        # コンポーネント初期化
        self.config = None
        self.auth = None
        self.url_manager = None
        self.recording_engine = None
        self.monitor = None
        self.system_monitor = None
        
        # 制御フラグ
        self.running = False
        self.shutdown_event = threading.Event()
        
        # シグナルハンドラー設定
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """シグナルハンドラー"""
        self.logger.info(f"シグナル {signum} 受信。終了処理開始...")
        self.shutdown_event.set()
    
    async def initialize(self) -> bool:
        """システム初期化"""
        try:
            self.logger.info("="*60)
            self.logger.info("🎬 ラクロク TwitCasting v2.0 起動中...")
            self.logger.info("="*60)
            
            # 設定管理は既に初期化済み
            self.config = self.config_manager
            self.logger.info("✅ 設定管理初期化完了")
            
            # 依存関係チェック
            if not await self._check_dependencies():
                return False
            
            # 他のモジュールを遅延インポート
            try:
                from auth_core import TwitCastingAuth, LimitedStreamAuth
                from recording_engine import RecordingEngine, RecordingMethod
                from twitcasting_monitor import TwitCastingMonitor, StreamStatus
                
                # 認証管理初期化
                self.auth = TwitCastingAuth(self.base_dir)
                self.logger.info("✅ 認証管理初期化完了")
                
                # URL管理初期化
                self.url_manager = URLManager(self.config)
                self.logger.info("✅ URL管理初期化完了")
                
                # 録画エンジン初期化（認証付き対応）
                try:
                    # 認証付き録画エンジンを試行
                    from authenticated_recording import AuthenticatedRecordingEngine
                    self.recording_engine = AuthenticatedRecordingEngine(self.config, self.system_config)
                    self.logger.info("✅ 認証付き録画エンジン初期化完了")
                except ImportError:
                    # 通常の録画エンジンにフォールバック
                    self.recording_engine = RecordingEngine(self.config, self.auth)
                    self.logger.info("✅ 録画エンジン初期化完了")
                except Exception as e:
                    self.logger.warning(f"⚠️ 録画エンジン初期化失敗: {e}")
                    self.logger.info("録画機能なしで続行します")
                    self.recording_engine = None
                
                # 監視システム初期化
                try:
                    self.monitor = TwitCastingMonitor(self.config, self.auth, self.recording_engine)
                    self.logger.info("✅ 監視システム初期化完了")
                except Exception as e:
                    self.logger.warning(f"⚠️ 監視システム初期化失敗: {e}")
                    self.logger.info("監視機能なしで続行します")
                    self.monitor = None
                
            except ImportError as e:
                self.logger.warning(f"⚠️ 一部モジュールが見つかりません: {e}")
                self.logger.info("基本機能のみで続行します")
            
            # システム監視初期化
            self.system_monitor = SystemMonitor(self.system_config)
            self.logger.info("✅ システム監視初期化完了")
            
            # 初期URL読み込み
            await self._load_initial_urls()
            
            # Cookie初期化（必要に応じて）
            if not self.args.skip_auth and self.auth:
                await self._initialize_auth()
            
            self.logger.info("🚀 システム初期化完了")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 初期化エラー: {e}")
            return False
    
    async def _check_dependencies(self) -> bool:
        """依存関係チェック"""
        self.logger.info("🔍 依存関係チェック中...")
        
        # DependencyCheckerを使用
        deps_checker = DependencyChecker()
        deps = await deps_checker.check_all_dependencies()
        
        missing_deps = []
        
        # 必須依存関係チェック
        for name, result in deps.get('required', {}).items():
            if result.get('available', False):
                self.logger.info(f"✅ {name}: {result.get('version', 'OK')}")
            else:
                self.logger.error(f"❌ {name}: {result.get('error', '不明なエラー')}")
                missing_deps.append(name)
        
        # オプション依存関係チェック
        for name, result in deps.get('optional', {}).items():
            if result.get('available', False):
                self.logger.info(f"✅ {name} (オプション): {result.get('version', 'OK')}")
            else:
                self.logger.warning(f"⚠️ {name} (オプション): {result.get('error', '利用不可')}")
        
        if missing_deps:
            self.logger.error(f"不足している依存関係: {', '.join(missing_deps)}")
            self.logger.info("以下のコマンドでインストールしてください:")
            for dep in missing_deps:
                if dep == 'streamlink':
                    self.logger.info("  pip install streamlink")
                elif dep == 'yt-dlp':
                    self.logger.info("  pip install yt-dlp")
                elif dep == 'ffmpeg':
                    self.logger.info("  https://ffmpeg.org/download.html からダウンロード")
            
            if not self.args.auto_install:
                return False
        
        return True
    
    async def _load_initial_urls(self):
        """初期URL読み込み"""
        if not self.url_manager:
            self.logger.warning("URL管理が利用できません")
            return
            
        urls = self.url_manager.get_active_urls()
        
        if not urls and not self.args.headless:
            self.logger.warning("監視対象URLが設定されていません")
            # サンプルURL追加（デモ用）
            sample_urls = [
                "https://twitcasting.tv/c:vau1013",
                "https://twitcasting.tv/vau0307"
            ]
            for url in sample_urls:
                success = self.url_manager.add_url(url, f"サンプルURL: {url}")
                if success:
                    self.logger.info(f"サンプルURL追加: {url}")
            urls = self.url_manager.get_active_urls()
        
        # 監視システムにURL追加
        if self.monitor:
            for url_entry in urls:
                url = url_entry.get('url', '')
                self.monitor.add_stream(url, None)  # パスワードは後で実装
        
        self.logger.info(f"📋 監視対象URL: {len(urls)}件")
        for i, url_entry in enumerate(urls, 1):
            url = url_entry.get('url', '')
            username = url.split('/')[-1] if url else 'unknown'
            self.logger.info(f"  {i}. {username}")
    
    async def _initialize_auth(self):
        """認証初期化"""
        self.logger.info("🔐 認証状態確認中...")
        
        try:
            if hasattr(self.auth, 'needs_refresh') and self.auth.needs_refresh():
                self.logger.info("Cookie更新が必要です")
                
                headless = self.args.headless if hasattr(self.args, 'headless') else True
                
                if hasattr(self.auth, 'auto_refresh_if_needed'):
                    success = await self.auth.auto_refresh_if_needed(headless)
                    
                    if success:
                        self.logger.info("✅ Cookie更新成功")
                    else:
                        self.logger.warning("⚠️ Cookie更新失敗（一部機能が制限される可能性があります）")
                else:
                    self.logger.warning("⚠️ 自動認証機能が利用できません")
            else:
                self.logger.info("✅ 認証状態正常")
        except Exception as e:
            self.logger.warning(f"⚠️ 認証初期化エラー: {e}")
    
    async def run_interactive_mode(self):
        """対話モード実行"""
        self.logger.info("🎮 対話モード開始")
        self.logger.info("-" * 40)
        
        print("\n📋 利用可能なコマンド:")
        print("  start  - 監視開始")
        print("  stop   - 監視停止") 
        print("  add    - URL追加")
        print("  auth-add - 年齢制限配信追加（ブラウザ認証付き）")
        print("  list   - URL一覧")
        print("  status - 状態確認")
        print("  stats  - 統計情報")
        print("  test   - システムテスト")
        print("  quit   - 終了")
        print()
        
        while not self.shutdown_event.is_set():
            try:
                command = input("ラクロク> ").strip().lower()
                
                if command == "start":
                    await self._cmd_start()
                elif command == "stop":
                    await self._cmd_stop()
                elif command == "add":
                    await self._cmd_add_url()
                elif command == "auth-add":
                    await self._cmd_auth_add_url()
                elif command == "list":
                    await self._cmd_list_urls()
                elif command == "status":
                    await self._cmd_status()
                elif command == "stats":
                    await self._cmd_stats()
                elif command == "test":
                    await self._cmd_test()
                elif command in ["quit", "exit", "q"]:
                    break
                elif command == "help":
                    print("利用可能なコマンド: start, stop, add, auth-add, list, status, stats, test, quit")
                else:
                    print(f"不明なコマンド: {command}")
            
            except (EOFError, KeyboardInterrupt):
                break
            except Exception as e:
                self.logger.error(f"コマンド実行エラー: {e}")
    
    async def run_daemon_mode(self):
        """デーモンモード実行"""
        self.logger.info("🤖 デーモンモード開始")
        
        # 自動監視開始
        if self.monitor:
            self.monitor.start_monitoring()
        
        # システム監視ループ
        while not self.shutdown_event.is_set():
            try:
                # システム状態更新
                if self.system_monitor:
                    status = self.system_monitor.get_status()
                    self.logger.debug(f"システム状態: CPU {status.get('cpu_percent', 0):.1f}%")
                
                # 30秒待機
                await asyncio.sleep(30)
                
            except Exception as e:
                self.logger.error(f"デーモンループエラー: {e}")
                await asyncio.sleep(60)
    
    async def _cmd_start(self):
        """監視開始コマンド"""
        if not self.monitor:
            print("❌ 監視システムが利用できません")
            return
            
        if not hasattr(self.monitor, 'monitoring') or not self.monitor.monitoring:
            if hasattr(self.monitor, 'start_monitoring'):
                self.monitor.start_monitoring()
                print("✅ 監視開始")
            else:
                print("❌ 監視開始機能が利用できません")
        else:
            print("⚠️ 既に監視中です")
    
    async def _cmd_stop(self):
        """監視停止コマンド"""
        if not self.monitor:
            print("❌ 監視システムが利用できません")
            return
            
        if hasattr(self.monitor, 'monitoring') and self.monitor.monitoring:
            if hasattr(self.monitor, 'stop_monitoring'):
                self.monitor.stop_monitoring()
                print("✅ 監視停止")
            else:
                print("❌ 監視停止機能が利用できません")
        else:
            print("⚠️ 監視していません")
    
    async def _cmd_add_url(self):
        """URL追加コマンド"""
        if not self.url_manager:
            print("❌ URL管理が利用できません")
            return
            
        try:
            url = input("TwitCasting URL: ").strip()
            description = input("説明（任意）: ").strip()
            
            if self.url_manager.add_url(url, description):
                if self.monitor and hasattr(self.monitor, 'add_stream'):
                    self.monitor.add_stream(url, None)
                print(f"✅ URL追加: {url.split('/')[-1]}")
            else:
                print("❌ URL追加失敗")
        except (EOFError, KeyboardInterrupt):
            print("\nキャンセルしました")
    
    async def _cmd_auth_add_url(self):
        """年齢制限配信追加コマンド（ブラウザ認証付き）"""
        try:
            url = input("年齢制限配信URL: ").strip()
            password = input("限定配信パスワード（不要な場合はEnter）: ").strip()
            
            # 認証付き録画エンジンを使用
            try:
                from authenticated_recording import AuthenticatedRecordingEngine
                auth_engine = AuthenticatedRecordingEngine(self.config_manager, self.system_config)
                
                print("🔐 ブラウザが開きます。必要に応じてTwitCastingにログインしてください...")
                print("⏰ 配信開始を待機します...")
                
                success = await auth_engine.start_authenticated_recording(
                    url, 
                    password if password else None
                )
                
                if success:
                    print(f"✅ 年齢制限配信録画開始: {url}")
                else:
                    print(f"❌ 録画開始失敗: {url}")
                    
            except ImportError:
                print("❌ 認証付き録画エンジンが利用できません")
                print("先に以下をインストールしてください:")
                print("pip install playwright")
                print("playwright install chromium")
                
        except (EOFError, KeyboardInterrupt):
            print("\nキャンセルしました")
    
    async def _cmd_list_urls(self):
        """URL一覧コマンド"""
        if not self.url_manager:
            print("❌ URL管理が利用できません")
            return
            
        urls = self.url_manager.get_active_urls()
        if urls:
            print(f"\n📋 監視対象URL ({len(urls)}件):")
            for i, url_entry in enumerate(urls, 1):
                url = url_entry.get('url', '')
                description = url_entry.get('description', '')
                username = url.split('/')[-1] if url else 'unknown'
                desc_text = f" - {description}" if description else ""
                print(f"  {i}. {username}{desc_text}")
        else:
            print("📋 監視対象URLはありません")
    
    async def _cmd_status(self):
        """状態確認コマンド"""
        print(f"\n📊 システム状態:")
        
        # 基本状態
        monitoring_status = "🟢 実行中" if (self.monitor and hasattr(self.monitor, 'monitoring') and self.monitor.monitoring) else "🔴 停止中"
        print(f"  監視状態: {monitoring_status}")
        
        # システム監視状態
        if self.system_monitor:
            status = self.system_monitor.get_status()
            print(f"  CPU使用率: {status.get('cpu_percent', 0):.1f}%")
            print(f"  メモリ使用率: {status.get('memory_percent', 0):.1f}%")
            print(f"  ディスク空き容量: {status.get('disk_free_gb', 0):.1f}GB")
        
        # URL状態
        if self.url_manager:
            urls = self.url_manager.get_active_urls()
            print(f"  監視URL数: {len(urls)}件")
    
    async def _cmd_stats(self):
        """統計情報コマンド"""
        print(f"\n📈 統計情報:")
        
        if self.url_manager:
            urls = self.url_manager.get_active_urls()
            print(f"  総監視配信: {len(urls)}件")
        
        if self.system_monitor:
            status = self.system_monitor.get_status()
            print(f"  最終確認: {status.get('last_check', '未確認')}")
    
    async def _cmd_test(self):
        """システムテストコマンド"""
        print("\n🧪 システムテスト実行中...")
        
        # config_coreのテスト関数を呼び出し
        try:
            from config_core import test_all_components
            result = await test_all_components()
            if result:
                print("✅ システムテスト完了")
            else:
                print("❌ システムテストで問題が検出されました")
        except Exception as e:
            print(f"❌ テスト実行エラー: {e}")
    
    async def shutdown(self):
        """終了処理"""
        self.logger.info("🛑 終了処理開始...")
        
        try:
            # 監視停止
            if self.monitor and hasattr(self.monitor, 'stop_monitoring'):
                self.monitor.stop_monitoring()
            
            # 録画エンジン停止
            if self.recording_engine and hasattr(self.recording_engine, 'shutdown'):
                if hasattr(self.recording_engine, 'shutdown'):
                    if asyncio.iscoroutinefunction(self.recording_engine.shutdown):
                        await self.recording_engine.shutdown()
                    else:
                        self.recording_engine.shutdown()
            
            # システム監視停止
            if self.system_monitor and hasattr(self.system_monitor, 'stop_monitoring'):
                await self.system_monitor.stop_monitoring()
            
            # 設定保存
            if self.config_manager:
                self.config_manager.save_system_config()
                self.config_manager.save_recording_config()
                self.config_manager.save_urls()
            
            self.logger.info("✅ 終了処理完了")
            
        except Exception as e:
            self.logger.error(f"終了処理エラー: {e}")


def parse_arguments():
    """コマンドライン引数解析"""
    parser = argparse.ArgumentParser(description="ラクロク TwitCasting 自動録画システム")
    
    parser.add_argument("--config-dir", help="設定ディレクトリパス")
    parser.add_argument("--daemon", action="store_true", help="デーモンモード実行")
    parser.add_argument("--headless", action="store_true", help="ヘッドレスモード（ブラウザ非表示）")
    parser.add_argument("--skip-auth", action="store_true", help="認証初期化をスキップ")
    parser.add_argument("--auto-install", action="store_true", help="不足パッケージの自動インストール")
    parser.add_argument("--debug", action="store_true", help="デバッグモード")
    
    # URL管理コマンド
    parser.add_argument("--add-url", help="URL追加して終了")
    parser.add_argument("--list-urls", action="store_true", help="URL一覧表示して終了")
    
    return parser.parse_args()


async def main():
    """メイン関数"""
    args = parse_arguments()
    
    # デバッグモード
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # メインアプリケーション初期化
    app = RakurekoMain(args)
    
    try:
        # システム初期化
        if not await app.initialize():
            print("❌ 初期化失敗")
            return 1
        
        # URL管理コマンド処理
        if args.add_url:
            if app.url_manager and app.url_manager.add_url(args.add_url):
                print(f"✅ URL追加: {args.add_url}")
            else:
                print(f"❌ URL追加失敗: {args.add_url}")
            return 0
        
        if args.list_urls:
            if app.url_manager:
                urls = app.url_manager.get_active_urls()
                if urls:
                    print(f"監視対象URL ({len(urls)}件):")
                    for i, url_entry in enumerate(urls, 1):
                        url = url_entry.get('url', '')
                        print(f"  {i}. {url}")
                else:
                    print("監視対象URLはありません")
            else:
                print("❌ URL管理が利用できません")
            return 0
        
        # メイン実行
        if args.daemon:
            await app.run_daemon_mode()
        else:
            await app.run_interactive_mode()
        
        return 0
        
    except KeyboardInterrupt:
        print("\n👋 ユーザー操作により終了")
        return 0
    except Exception as e:
        app.logger.error(f"予期しないエラー: {e}")
        return 1
    finally:
        await app.shutdown()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 終了")
        sys.exit(0)