#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - RakurekoTwitCasting Phase 1認証フロー修正版
限定配信録画対応の緊急修正
"""

import asyncio
import sys
import os
import logging
import signal
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Protocol
from datetime import datetime
from dataclasses import dataclass
import argparse

# ログ設定
def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """最適化ログシステム"""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # ログフォーマット
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    
    # ルートロガー設定
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # 既存ハンドラークリア
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    
    # ファイルハンドラー
    file_handler = logging.FileHandler('rakureko_twitcasting.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # コンソールハンドラー
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    logger = logging.getLogger('**main**')
    logger.info("リファクタリング版ログシステム初期化完了")
    return logger

logger = setup_logging()

# プロジェクトのsrcディレクトリをパスに追加
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

# 設定・依存関係インポート
try:
    from config_core import SystemConfig, ConfigManager, DependencyChecker
    logger.info("✅ 正規SystemConfig統合完了")
except ImportError as e:
    logger.error(f"❌ config_core インポートエラー: {e}")
    sys.exit(1)

# プロトコル定義（疎結合化）
class RecordingEngineProtocol(Protocol):
    """録画エンジンプロトコル（インターフェース）"""
    async def start_recording(self, url: str, options: Optional[Dict] = None) -> bool: ...
    async def stop_recording(self, url: str) -> bool: ...
    def get_active_recordings(self) -> Dict[str, Any]: ...
    async def cleanup(self) -> None: ...

class URLAnalyzerProtocol(Protocol):
    """URL解析エンジンプロトコル"""
    async def analyze_url(self, url: str) -> Dict[str, Any]: ...
    async def cleanup(self) -> None: ...

class AuthenticatedRecorderProtocol(Protocol):
    """認証録画エンジンプロトコル"""
    async def start_authenticated_recording(self, url: str, options: Any) -> bool: ...
    def get_active_recordings(self) -> Dict[str, Any]: ...
    async def shutdown(self) -> None: ...

# 状態管理の分離
@dataclass
class SystemState:
    """システム状態管理（分離された状態クラス）"""
    running: bool = True
    daemon_mode: bool = False
    initialization_complete: bool = False
    shutdown_in_progress: bool = False
    system_start_time: datetime = datetime.now()
    
    def is_operational(self) -> bool:
        """システムが運用可能か"""
        return self.running and self.initialization_complete and not self.shutdown_in_progress

@dataclass
class RecordingSessionInfo:
    """録画セッション情報（責務を明確化）"""
    url: str
    user_id: str
    session_id: str
    start_time: datetime
    engine_type: str = "auto"  # authenticated/basic/auto
    status: str = "initializing"
    
    def get_duration(self) -> str:
        """録画時間取得"""
        duration = datetime.now() - self.start_time
        return str(duration).split('.')[0]

# Orchestrator化されたメインアプリケーション
class RakurekoTwitCastingOrchestrator:
    """
    Phase 1認証フロー修正版オーケストレーター
    - 限定配信録画に特化した修正
    - auth_core統合の最適化
    """
    
    def __init__(self, system_config: SystemConfig):
        self.system_config = system_config
        self.state = SystemState()
        
        # エンジン参照（プロトコルベース）
        self.config_manager: Optional[ConfigManager] = None
        self.url_analyzer: Optional[URLAnalyzerProtocol] = None
        self.authenticated_recorder: Optional[AuthenticatedRecorderProtocol] = None
        self.recording_engine: Optional[RecordingEngineProtocol] = None
        self.dependency_checker: Optional[DependencyChecker] = None
        
        # セッション管理をシンプル化
        self.active_sessions: Dict[str, RecordingSessionInfo] = {}
        self.background_tasks: List[asyncio.Task] = []
        
        # シグナルハンドラー
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except (ValueError, AttributeError):
            logger.warning("シグナルハンドラー設定スキップ（非コンソール環境）")
        
        logger.info("🚀 オーケストレーター初期化完了")
    
    def _signal_handler(self, signum, frame):
        """シグナルハンドラー"""
        if self.state.shutdown_in_progress:
            return
        
        logger.info(f"🛑 終了シグナル受信: {signum}")
        self.state.running = False
    
    async def initialize(self) -> bool:
        """システム初期化（オーケストレーション）"""
        logger.info("=" * 80)
        logger.info("🎬 RakurekoTwitCasting 完全リファクタリング版 初期化開始")
        logger.info("=" * 80)
        
        try:
            # Phase 1: 依存関係チェック
            logger.info("📋 Phase 1: 依存関係チェック")
            if not await self._check_dependencies():
                return False
            
            # Phase 2: 設定管理初期化
            logger.info("📋 Phase 2: 設定管理初期化")
            if not await self._initialize_config_manager():
                return False
            
            # Phase 3: エンジン初期化（疎結合）
            logger.info("📋 Phase 3: エンジン初期化")
            await self._initialize_engines()
            
            # Phase 4: ディレクトリ準備
            logger.info("📋 Phase 4: ディレクトリ準備")
            self._ensure_directories()
            
            # Phase 5: バックグラウンドタスク開始
            logger.info("📋 Phase 5: バックグラウンドタスク開始")
            await self._start_background_tasks()
            
            self.state.initialization_complete = True
            
            # 初期化完了レポート
            self._log_initialization_report()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 初期化エラー: {e}", exc_info=True)
            return False
    
    async def _check_dependencies(self) -> bool:
        """依存関係チェック"""
        try:
            self.dependency_checker = DependencyChecker()
            results = await self.dependency_checker.check_all_dependencies()
            
            # 必須依存関係チェック
            critical_ok = all(
                dep['available'] for dep in results.get('required', {}).values()
            )
            
            if not critical_ok:
                logger.error("❌ 必須依存関係不足")
                return False
            
            logger.info("✅ 依存関係チェック完了")
            return True
            
        except Exception as e:
            logger.error(f"依存関係チェックエラー: {e}")
            return False
    
    async def _initialize_config_manager(self) -> bool:
        """設定管理初期化"""
        try:
            self.config_manager = ConfigManager()
            
            # 設定ファイル作成・検証
            if not self.config_manager.config_file_exists():
                await self.config_manager.create_default_config()
            
            await self.config_manager.load_config()
            
            validation_result = await self.config_manager.validate_config()
            if not validation_result['valid']:
                logger.warning(f"設定検証問題: {validation_result['issues']}")
                await self.config_manager.auto_repair_config()
            
            logger.info("✅ 設定管理初期化完了")
            return True
            
        except Exception as e:
            logger.error(f"設定管理初期化エラー: {e}")
            return False
    
    async def _initialize_engines(self):
        """エンジン初期化（疎結合アプローチ）"""
        # URL解析エンジン
        try:
            from url_analyzer import URLAnalyzer
            self.url_analyzer = URLAnalyzer()
            logger.info("✅ URL解析エンジン初期化完了")
        except ImportError as e:
            logger.warning(f"URL解析エンジン初期化失敗: {e}")
        
        # 認証録画エンジンの初期化改良
        try:
            from authenticated_recording import AuthenticatedRecordingEngine
            self.authenticated_recorder = AuthenticatedRecordingEngine(
                self.config_manager, 
                self.system_config
            )
            logger.info("✅ 認証録画エンジン初期化完了")
        except ImportError as e:
            logger.error(f"❌ 認証録画エンジン初期化失敗: {e}")
            logger.error("限定配信録画が利用できません")
        
        # 基本録画エンジン
        try:
            from recording_engine import RecordingEngine
            if self.config_manager:
                self.recording_engine = RecordingEngine(self.config_manager)
                logger.info("✅ 基本録画エンジン初期化完了")
        except ImportError as e:
            logger.warning(f"基本録画エンジン初期化失敗: {e}")
    
    def _ensure_directories(self):
        """ディレクトリ確保"""
        try:
            directories = [
                self.system_config.recordings_dir,
                self.system_config.data_dir,
                self.system_config.logs_dir,
                self.system_config.recordings_dir / "temp"
            ]
            
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"📁 ディレクトリ準備完了: recordings")
            
        except Exception as e:
            logger.error(f"ディレクトリ作成エラー: {e}")
    
    async def _start_background_tasks(self):
        """バックグラウンドタスク開始"""
        # 統計更新タスク
        stats_task = asyncio.create_task(self._periodic_stats_update())
        self.background_tasks.append(stats_task)
        
        logger.info(f"🔄 バックグラウンドタスク開始: {len(self.background_tasks)}個")
    
    async def _periodic_stats_update(self):
        """定期統計更新"""
        while self.state.is_operational():
            try:
                await asyncio.sleep(60)
                # 統計更新処理
                logger.debug(f"📊 統計更新: アクティブセッション={len(self.active_sessions)}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"統計更新エラー: {e}")
    
    def _log_initialization_report(self):
        """初期化完了レポート"""
        logger.info("🎉 初期化完了レポート:")
        logger.info(f"  ⚙️ URL解析エンジン: {'✅' if self.url_analyzer else '❌'}")
        logger.info(f"  🔐 認証録画エンジン: {'✅' if self.authenticated_recorder else '❌'}")
        logger.info(f"  📹 基本録画エンジン: {'✅' if self.recording_engine else '❌'}")
        logger.info(f"  🔧 設定管理: {'✅' if self.config_manager else '❌'}")
        logger.info(f"  📊 システム状態: 運用可能")
    
    async def start_recording(self, url: str, options: Optional[Dict] = None) -> bool:
        """録画開始（Phase 1認証フロー修正版）"""
        try:
            logger.info(f"🎬 録画開始要求: {url}")
            
            # 重複チェック
            if any(session.url == url for session in self.active_sessions.values()):
                print(f"⚠️ 既に録画中: {url}")
                return False
            
            # 同時録画数制限
            if len(self.active_sessions) >= self.system_config.max_concurrent_recordings:
                print(f"⚠️ 同時録画数上限: {len(self.active_sessions)}/{self.system_config.max_concurrent_recordings}")
                return False
            
            # URL解析による認証要件判定の改良
            analysis_result = await self._analyze_url_if_available(url)
            
            # セッション情報作成
            session = self._create_recording_session(url, analysis_result)
            self.active_sessions[session.session_id] = session
            
            # 認証要件に基づく適切なエンジン選択
            success = await self._delegate_recording_to_engine_improved(session, options, analysis_result)
            
            if success:
                session.status = "recording"
                print(f"✅ 録画開始成功: {session.user_id}")
                logger.info(f"録画開始成功: {session.user_id} (エンジン: {session.engine_type})")
                return True
            else:
                # 失敗時のクリーンアップ
                del self.active_sessions[session.session_id]
                print(f"❌ 録画開始失敗: {session.user_id}")
                return False
                
        except Exception as e:
            logger.error(f"録画開始エラー: {url} - {e}", exc_info=True)
            return False
    
    async def _analyze_url_if_available(self, url: str) -> Optional[Dict[str, Any]]:
        """URL解析（利用可能な場合）"""
        if not self.url_analyzer:
            return None
        
        try:
            print(f"🔍 URL解析中: {url}")
            analysis = await self.url_analyzer.analyze_url(url)
            
            if analysis.get('valid'):
                print(f"✅ URL解析完了: {analysis.get('broadcaster', 'Unknown')}")
                return analysis
            else:
                print(f"❌ URL解析失敗: {analysis.get('error', 'Unknown error')}")
                return None
                
        except Exception as e:
            logger.warning(f"URL解析エラー（録画継続）: {e}")
            return None
    
    def _create_recording_session(self, url: str, analysis_result: Optional[Dict]) -> RecordingSessionInfo:
        """録画セッション作成"""
        import time
        
        user_id = "unknown"
        if analysis_result:
            user_id = analysis_result.get('broadcaster', 'unknown')
        else:
            # フォールバック: URLからユーザーID抽出
            import re
            match = re.search(r'twitcasting\.tv/([^/\?]+)', url)
            if match:
                user_id = match.group(1)
        
        session_id = f"session_{int(time.time())}_{len(self.active_sessions)}"
        
        return RecordingSessionInfo(
            url=url,
            user_id=user_id,
            session_id=session_id,
            start_time=datetime.now()
        )
    
    async def _delegate_recording_to_engine_improved(self, session: RecordingSessionInfo, 
                                                   options: Optional[Dict], 
                                                   analysis_result: Optional[Dict]) -> bool:
        """改良されたエンジン委譲ロジック"""
        # 認証要件判定の改良
        requires_auth = self._determine_auth_requirement(analysis_result, session.url)
        
        logger.info(f"🔍 認証要件判定: {session.user_id} -> 認証{'必要' if requires_auth else '不要'}")
        
        # 認証録画エンジンを優先する戦略
        if requires_auth:
            if self.authenticated_recorder:
                print("🔐 認証付き録画エンジン使用")
                return await self._start_with_authenticated_engine(session, options)
            else:
                print("❌ 認証録画エンジンが利用できません（限定配信録画不可）")
                # フォールバック: 基本エンジンで試行
                if self.recording_engine:
                    print("⚠️ 基本録画エンジンで試行（成功率低）")
                    return await self._start_with_basic_engine(session, options)
                return False
        else:
            # 通常配信: 基本エンジンを優先
            if self.recording_engine:
                print("📹 基本録画エンジン使用")
                return await self._start_with_basic_engine(session, options)
            elif self.authenticated_recorder:
                print("🔐 認証付き録画エンジン使用（フォールバック）")
                return await self._start_with_authenticated_engine(session, options)
            else:
                print("❌ 利用可能な録画エンジンがありません")
                return False
    
    def _determine_auth_requirement(self, analysis_result: Optional[Dict], url: str) -> bool:
        """認証要件判定の改良"""
        # URL解析結果がある場合
        if analysis_result:
            return analysis_result.get('requires_auth', False)
        
        # URL解析結果がない場合のフォールバック判定
        url_lower = url.lower()
        
        # グループ配信の判定
        if '/g:' in url or 'group' in url_lower:
            logger.info("🔍 グループ配信URLを検出 -> 認証必要")
            return True
        
        # コミュニティ配信の判定
        if '/c:' in url or 'community' in url_lower:
            logger.info("🔍 コミュニティ配信URLを検出 -> 認証必要")
            return True
        
        # その他の限定配信の可能性
        limited_indicators = ['limited', 'private', 'member']
        if any(indicator in url_lower for indicator in limited_indicators):
            logger.info("🔍 限定配信URLを検出 -> 認証必要")
            return True
        
        # デフォルトは通常配信
        return False
    
    async def _start_with_authenticated_engine(self, session: RecordingSessionInfo, 
                                             options: Optional[Dict]) -> bool:
        """認証付きエンジンで録画開始"""
        try:
            from recording_options import RecordingOptions
            
            session.engine_type = "authenticated"
            
            # RecordingOptionsの適切な設定
            recording_options = RecordingOptions(
                confirmed_by_user=True,
                headless=True,
                quality="best",
                session_name=session.session_id,
                timeout_minutes=180,
                max_retries=3
            )
            
            # パスワード設定
            if options and 'password' in options:
                recording_options.password = options['password']
                logger.info(f"🔑 パスワード設定済み: {session.user_id}")
            
            return await self.authenticated_recorder.start_authenticated_recording(
                session.url, recording_options
            )
            
        except Exception as e:
            logger.error(f"認証付きエンジン録画エラー: {e}")
            return False
    
    async def _start_with_basic_engine(self, session: RecordingSessionInfo, 
                                     options: Optional[Dict]) -> bool:
        """基本エンジンで録画開始"""
        try:
            session.engine_type = "basic"
            
            password = options.get('password') if options else None
            return await self.recording_engine.start_recording(session.url, password)
            
        except Exception as e:
            logger.error(f"基本エンジン録画エラー: {e}")
            return False
    
    async def stop_recording(self, url: str) -> bool:
        """録画停止（オーケストレーション）"""
        try:
            # セッション検索
            target_session = None
            for session in self.active_sessions.values():
                if session.url == url:
                    target_session = session
                    break
            
            if not target_session:
                print(f"⚠️ 指定URLの録画が見つかりません: {url}")
                return False
            
            logger.info(f"⏹️ 録画停止要求: {target_session.user_id}")
            target_session.status = "stopping"
            
            # エンジン別停止処理
            success = await self._delegate_stop_to_engine(target_session)
            
            # セッション後処理
            if success:
                target_session.status = "stopped"
                print(f"✅ 録画停止完了: {target_session.user_id}")
            else:
                target_session.status = "stop_failed"
                print(f"❌ 録画停止失敗: {target_session.user_id}")
            
            # アクティブセッションから削除
            del self.active_sessions[target_session.session_id]
            
            return success
            
        except Exception as e:
            logger.error(f"録画停止エラー: {url} - {e}")
            return False
    
    async def _delegate_stop_to_engine(self, session: RecordingSessionInfo) -> bool:
        """停止をエンジンに委譲"""
        try:
            if session.engine_type == "authenticated" and self.authenticated_recorder:
                return await self.authenticated_recorder.stop_recording(session.url)
            elif session.engine_type == "basic" and self.recording_engine:
                return await self.recording_engine.stop_recording(session.url)
            else:
                return True  # 基本的には成功とみなす
                
        except Exception as e:
            logger.error(f"エンジン停止委譲エラー: {e}")
            return False
    
    def list_recordings(self):
        """録画一覧表示"""
        if not self.active_sessions:
            print("📭 実行中の録画はありません")
            return
        
        print("📋 実行中の録画一覧:")
        print("=" * 70)
        
        for i, session in enumerate(self.active_sessions.values(), 1):
            engine_icon = {"authenticated": "🔐", "basic": "📹", "auto": "⚙️"}.get(session.engine_type, "❓")
            status_icon = {"recording": "🔴", "initializing": "🟡", "stopping": "🟠"}.get(session.status, "⚪")
            
            print(f"  {i}. {status_icon} {engine_icon} {session.user_id}")
            print(f"     🔗 URL: {session.url}")
            print(f"     ⏱️  経過時間: {session.get_duration()}")
            print(f"     📊 状態: {session.status}")
            print(f"     🎛️  エンジン: {session.engine_type}")
            print("     " + "-" * 60)
    
    def show_status(self):
        """システム状態表示"""
        uptime = datetime.now() - self.state.system_start_time
        uptime_str = str(uptime).split('.')[0]
        
        print("📊 RakurekoTwitCasting リファクタリング版 システム状態")
        print("=" * 80)
        
        # システム基本情報
        print("🔧 システム情報:")
        print(f"  🏗️  アーキテクチャ: オーケストレーター型（疎結合）")
        print(f"  ⏱️  稼働時間: {uptime_str}")
        print(f"  🎬 実行中録画: {len(self.active_sessions)} / {self.system_config.max_concurrent_recordings}")
        print(f"  📁 録画ディレクトリ: {self.system_config.recordings_dir}")
        print(f"  📊 システム状態: {'✅ 運用中' if self.state.is_operational() else '❌ 異常'}")
        
        # エンジン統合状態
        print("\n🛠️ エンジン統合状態:")
        engines = [
            ("URL解析エンジン", self.url_analyzer, "🔍"),
            ("認証録画エンジン", self.authenticated_recorder, "🔐"),
            ("基本録画エンジン", self.recording_engine, "📹"),
            ("設定管理", self.config_manager, "🔧")
        ]
        
        for name, engine, icon in engines:
            status = "✅ 統合済み" if engine else "❌ 未統合"
            print(f"  {icon} {name}: {status}")
        
        # バックグラウンドタスク
        active_tasks = [task for task in self.background_tasks if not task.done()]
        print(f"\n🔄 バックグラウンドタスク: {len(active_tasks)}個実行中")
    
    def show_help(self):
        """ヘルプ表示"""
        print("""
🎌 RakurekoTwitCasting リファクタリング版 コマンド一覧

📹 録画関連:
  record <URL>              - 最適化録画開始（自動エンジン選択）
  stop <URL>                - 指定URLの録画停止
  list                      - 実行中録画一覧
  
🔍 分析・管理:
  analyze <URL>             - URL解析
  status                    - システム状態表示
  
🛠️ システム管理:
  test                      - システムテスト
  cleanup                   - 一時ファイルクリーンアップ
  
🆘 ヘルプ・終了:
  help                      - このヘルプ
  quit/exit                 - システム終了

💡 リファクタリング版特徴:
  - オーケストレーター型アーキテクチャ
  - プロトコルベース疎結合
  - 責務分離による保守性向上
        """)
    
    async def analyze_url(self, url: str):
        """URL解析"""
        if not self.url_analyzer:
            print("❌ URL解析エンジンが利用できません")
            return
        
        try:
            print(f"🔍 URL解析実行: {url}")
            analysis = await self.url_analyzer.analyze_url(url)
            
            print("📋 解析結果:")
            print(f"  📺 配信者: {analysis.get('broadcaster', 'Unknown')}")
            print(f"  📊 配信種別: {analysis.get('stream_type', 'Unknown')}")
            print(f"  🔴 配信状態: {'ライブ中' if analysis.get('is_live') else 'オフライン'}")
            print(f"  🔒 制限事項: {analysis.get('restrictions', 'なし')}")
            
            if analysis.get('requires_auth'):
                print("  🔐 認証が必要な配信です")
            
        except Exception as e:
            logger.error(f"URL解析エラー: {e}")
            print(f"❌ URL解析失敗: {e}")
    
    async def run_system_test(self):
        """システムテスト"""
        print("🧪 リファクタリング版システムテスト開始")
        print("=" * 60)
        
        tests = [
            ("システム状態", self._test_system_state()),
            ("エンジン統合", self._test_engine_integration()),
            ("設定管理", self._test_config_management()),
            ("ディレクトリ構造", self._test_directory_structure())
        ]
        
        results = []
        for test_name, test_coro in tests:
            try:
                print(f"🔍 テスト実行: {test_name}")
                result = await test_coro
                
                status = "✅ 合格" if result['passed'] else "❌ 不合格"
                print(f"  {status}: {result.get('message', 'テスト完了')}")
                results.append(result['passed'])
                
            except Exception as e:
                print(f"  ❌ テスト例外: {e}")
                results.append(False)
        
        # 結果サマリー
        passed = sum(results)
        total = len(results)
        success_rate = passed / total if total > 0 else 0
        
        print(f"\n📊 テスト結果: {passed}/{total} ({success_rate:.1%})")
        
        if success_rate >= 0.9:
            print("🎉 リファクタリング版システムは完璧に動作しています！")
        elif success_rate >= 0.7:
            print("✅ システムは正常動作中")
        else:
            print("❌ システムに問題があります")
    
    async def _test_system_state(self):
        """システム状態テスト"""
        return {
            'passed': self.state.is_operational(),
            'message': f'システム状態: {"運用可能" if self.state.is_operational() else "異常"}'
        }
    
    async def _test_engine_integration(self):
        """エンジン統合テスト"""
        integrated_count = sum([
            1 if self.url_analyzer else 0,
            1 if self.authenticated_recorder else 0,
            1 if self.recording_engine else 0,
            1 if self.config_manager else 0
        ])
        
        integration_rate = integrated_count / 4
        
        return {
            'passed': integration_rate >= 0.5,
            'message': f'エンジン統合率: {integration_rate:.1%} ({integrated_count}/4)'
        }
    
    async def _test_config_management(self):
        """設定管理テスト"""
        return {
            'passed': self.config_manager is not None,
            'message': f'設定管理: {"正常" if self.config_manager else "未統合"}'
        }
    
    async def _test_directory_structure(self):
        """ディレクトリ構造テスト"""
        try:
            required_dirs = [
                self.system_config.recordings_dir,
                self.system_config.data_dir,
                self.system_config.logs_dir
            ]
            
            all_exist = all(d.exists() for d in required_dirs)
            
            return {
                'passed': all_exist,
                'message': f'ディレクトリ構造: {"正常" if all_exist else "不完全"}'
            }
        except Exception as e:
            return {
                'passed': False,
                'message': f'ディレクトリテストエラー: {e}'
            }
    
    async def interactive_mode(self):
        """リファクタリング版対話モード"""
        print("🎌 RakurekoTwitCasting リファクタリング版へようこそ！")
        print("🏗️ オーケストレーター型アーキテクチャで動作中")
        print("💡 'help' でコマンド一覧を表示")
        print()
        
        while self.state.running:
            try:
                command = input("ラクロク[RF]> ").strip()
                
                if not command:
                    continue
                
                parts = command.split()
                cmd = parts[0].lower()
                args = parts[1:] if len(parts) > 1 else []
                
                # システム終了
                if cmd in ['quit', 'exit']:
                    print("👋 リファクタリング版システムを終了します...")
                    break
                
                # ヘルプ
                elif cmd == 'help':
                    self.show_help()
                
                # 録画開始
                elif cmd == 'record':
                    if not args:
                        print("❌ URLを指定してください")
                        print("   例: record https://twitcasting.tv/user_id")
                        continue
                    
                    url = args[0]
                    options = {}
                    
                    # パスワードオプション処理
                    if '--password' in args:
                        try:
                            pwd_index = args.index('--password')
                            if pwd_index + 1 < len(args):
                                options['password'] = args[pwd_index + 1]
                        except (ValueError, IndexError):
                            pass
                    
                    await self.start_recording(url, options)
                
                # 録画停止
                elif cmd == 'stop':
                    if not args:
                        print("❌ 停止するURLを指定してください")
                        continue
                    
                    url = args[0]
                    await self.stop_recording(url)
                
                # URL解析
                elif cmd == 'analyze':
                    if not args:
                        print("❌ 解析するURLを指定してください")
                        continue
                    
                    url = args[0]
                    await self.analyze_url(url)
                
                # 録画一覧
                elif cmd == 'list':
                    self.list_recordings()
                
                # システム状態
                elif cmd == 'status':
                    self.show_status()
                
                # システムテスト
                elif cmd == 'test':
                    await self.run_system_test()
                
                # クリーンアップ
                elif cmd == 'cleanup':
                    await self._cleanup_temp_files()
                    print("✅ 一時ファイルクリーンアップ完了")
                
                # 不明なコマンド
                else:
                    print(f"❌ 不明なコマンド: {cmd}")
                    print("💡 'help' でコマンド一覧を確認してください")
                
            except KeyboardInterrupt:
                print("\n👋 終了中...")
                break
            except Exception as e:
                logger.error(f"対話モードエラー: {e}", exc_info=True)
                print(f"❌ コマンド実行エラー: {e}")
    
    async def _cleanup_temp_files(self):
        """一時ファイルクリーンアップ"""
        try:
            temp_dir = self.system_config.recordings_dir / "temp"
            if temp_dir.exists():
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir.mkdir(exist_ok=True)
            
            logger.info("🧹 一時ファイルクリーンアップ完了")
            
        except Exception as e:
            logger.error(f"一時ファイルクリーンアップエラー: {e}")
    
    async def shutdown(self):
        """リファクタリング版システム終了処理"""
        if self.state.shutdown_in_progress:
            logger.warning("既に終了処理中です")
            return
        
        self.state.shutdown_in_progress = True
        logger.info("🛑 リファクタリング版システム終了処理開始...")
        
        try:
            # バックグラウンドタスク停止
            logger.info("🔄 バックグラウンドタスク停止中...")
            for task in self.background_tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
            
            # エンジンのシャットダウン（プロトコルベース）
            logger.info("🔧 エンジンシャットダウン中...")
            
            if self.authenticated_recorder and hasattr(self.authenticated_recorder, 'shutdown'):
                try:
                    await self.authenticated_recorder.shutdown()
                except Exception as e:
                    logger.error(f"認証録画エンジンシャットダウンエラー: {e}")
            
            if self.recording_engine and hasattr(self.recording_engine, 'cleanup'):
                try:
                    await self.recording_engine.cleanup()
                except Exception as e:
                    logger.error(f"基本録画エンジンクリーンアップエラー: {e}")
            
            if self.url_analyzer and hasattr(self.url_analyzer, 'cleanup'):
                try:
                    await self.url_analyzer.cleanup()
                except Exception as e:
                    logger.error(f"URL解析エンジンクリーンアップエラー: {e}")
            
            # 設定保存
            if self.config_manager:
                try:
                    self.config_manager.save_all_configs()
                    logger.info("💾 設定保存完了")
                except Exception as e:
                    logger.error(f"設定保存エラー: {e}")
            
            # 最終統計ログ
            uptime = datetime.now() - self.state.system_start_time
            logger.info("📊 最終統計:")
            logger.info(f"  システム種別: オーケストレーター型リファクタリング版")
            logger.info(f"  稼働時間: {uptime}")
            logger.info(f"  処理セッション数: {len(self.active_sessions)}")
            
            logger.info("✅ リファクタリング版システム終了処理完了")
            
        except Exception as e:
            logger.error(f"終了処理エラー: {e}", exc_info=True)
        finally:
            self.state.shutdown_in_progress = False

# ===============================
# コマンドライン引数処理
# ===============================

def create_argument_parser() -> argparse.ArgumentParser:
    """リファクタリング版引数パーサー"""
    parser = argparse.ArgumentParser(
        description='RakurekoTwitCasting リファクタリング版（限定配信対応）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Phase 1修正版特徴:
  - 認証付き録画エンジンとauth_core統合
  - グループ配信・年齢制限配信対応
  - yt-dlp + Cookie方式実装
  - エラーハンドリング強化

使用例:
  python main.py                              # 対話モード
  python main.py test                         # システムテスト
  python main.py https://twitcasting.tv/user  # 録画
  python main.py https://twitcasting.tv/g:123 # グループ配信録画
        """
    )
    
    parser.add_argument('command', nargs='?', help='実行コマンド (test/URL等)')
    parser.add_argument('--log-level', '-l', default='INFO',
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='ログレベル')
    parser.add_argument('--max-concurrent', '-c', type=int, default=3,
                       help='最大同時録画数')
    parser.add_argument('--output-dir', '-o', default='./recordings',
                       help='出力ディレクトリ')
    parser.add_argument('--password', '-p', help='限定配信パスワード')
    
    return parser

# ===============================
# メイン関数
# ===============================

async def main():
    """Phase 1修正版メイン関数"""
    # コマンドライン引数解析
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # ログレベル設定
    global logger
    logger = setup_logging(args.log_level)
    
    try:
        system_config = SystemConfig(
            recordings_dir=Path(args.output_dir),
            max_concurrent_recordings=args.max_concurrent,
            log_level=args.log_level
        )
    except Exception as e:
        logger.error(f"システム設定作成エラー: {e}")
        return 1
    
    # オーケストレーター初期化
    orchestrator = RakurekoTwitCastingOrchestrator(system_config)
    
    try:
        # システム初期化
        logger.info("🚀 RakurekoTwitCasting リファクタリング版起動")
        success = await orchestrator.initialize()
        
        if not success:
            print("❌ システム初期化に失敗しました。詳細はログを確認してください。")
            return 1
        
        # コマンド処理
        if args.command:
            if args.command == 'test':
                # システムテスト
                await orchestrator.run_system_test()
                
            elif args.command.startswith('http'):
                # 単一URL録画
                options = {}
                if args.password:
                    options['password'] = args.password
                    
                success = await orchestrator.start_recording(args.command, options)
                if success:
                    print("✅ 録画を開始しました。Ctrl+Cで停止できます。")
                    try:
                        while orchestrator.state.running and orchestrator.active_sessions:
                            await asyncio.sleep(1)
                    except KeyboardInterrupt:
                        print("\n📹 録画を停止します...")
                        await orchestrator.stop_recording(args.command)
                        
            else:
                print(f"❌ 不明なコマンド: {args.command}")
                return 1
        else:
            # 対話モード
            await orchestrator.interactive_mode()
        
        return 0
        
    except KeyboardInterrupt:
        logger.info("🛑 ユーザーによる中断")
        print("\n👋 リファクタリング版システムを終了します...")
        return 0
        
    except Exception as e:
        logger.error(f"メイン処理で予期しないエラー: {e}", exc_info=True)
        print(f"❌ 予期しないエラーが発生しました: {e}")
        return 1
        
    finally:
        # 確実にクリーンアップ実行
        try:
            await orchestrator.shutdown()
        except Exception as e:
            logger.error(f"終了処理エラー: {e}")

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 ユーザーによる終了")
        sys.exit(0)
    except Exception as e:
        print(f"❌ 致命的エラー: {e}")
        sys.exit(1)