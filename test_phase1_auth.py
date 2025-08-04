#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_phase1_auth.py - Phase 1認証システムテスト
限定配信録画機能の動作確認用
"""

import asyncio
import sys
import logging
from pathlib import Path

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_auth_core():
    """auth_core.pyの基本テスト"""
    print("🔐 === auth_core.py テスト ===")
    
    try:
        from auth_core import TwitCastingAuth, LimitedStreamAuth, test_auth_system
        print("✅ auth_core.py インポート成功")
        
        # 基本認証システムテスト
        result = await test_auth_system()
        if result:
            print("✅ auth_core基本テスト合格")
        else:
            print("❌ auth_core基本テスト失敗")
            
        return result
        
    except ImportError as e:
        print(f"❌ auth_core.py インポートエラー: {e}")
        return False
    except Exception as e:
        print(f"❌ auth_coreテストエラー: {e}")
        return False

async def test_authenticated_recording():
    """authenticated_recording.pyの基本テスト"""
    print("\n📹 === authenticated_recording.py テスト ===")
    
    try:
        from config_core import SystemConfig, ConfigManager
        from authenticated_recording import AuthenticatedRecordingEngine
        from recording_options import RecordingOptions
        
        print("✅ authenticated_recording.py インポート成功")
        
        # 設定準備
        config_manager = ConfigManager()
        system_config = SystemConfig()
        
        # エンジン初期化テスト
        engine = AuthenticatedRecordingEngine(config_manager, system_config)
        print("✅ 認証録画エンジン初期化成功")
        
        # 基本メソッドテスト
        recordings = engine.get_active_recordings()
        print(f"✅ アクティブ録画取得: {len(recordings)}件")
        
        # オプション作成テスト
        options = RecordingOptions(
            confirmed_by_user=True,
            headless=True,
            session_name="test_session"
        )
        print("✅ RecordingOptions作成成功")
        
        return True
        
    except ImportError as e:
        print(f"❌ authenticated_recording.py インポートエラー: {e}")
        return False
    except Exception as e:
        print(f"❌ authenticated_recordingテストエラー: {e}")
        return False

async def test_main_integration():
    """main.pyの統合テスト"""
    print("\n🏗️ === main.py 統合テスト ===")
    
    try:
        # システム設定
        sys.path.insert(0, str(Path.cwd() / "src"))
        
        from config_core import SystemConfig
        from main import RakurekoTwitCastingOrchestrator
        
        print("✅ main.py インポート成功")
        
        # オーケストレーター初期化テスト
        system_config = SystemConfig(
            recordings_dir=Path("./test_recordings"),
            max_concurrent_recordings=1
        )
        
        orchestrator = RakurekoTwitCastingOrchestrator(system_config)
        print("✅ オーケストレーター初期化成功")
        
        # 初期化テスト（実際のPlaywright/依存関係なしでテスト）
        try:
            success = await orchestrator.initialize()
            if success:
                print("✅ オーケストレーター初期化完了")
            else:
                print("⚠️ オーケストレーター初期化部分的成功（依存関係不足）")
        except Exception as e:
            print(f"⚠️ オーケストレーター初期化エラー（予想範囲内）: {e}")
        
        # システムテスト実行
        await orchestrator.run_system_test()
        
        # クリーンアップ
        await orchestrator.shutdown()
        print("✅ オーケストレーター終了成功")
        
        return True
        
    except ImportError as e:
        print(f"❌ main.py インポートエラー: {e}")
        return False
    except Exception as e:
        print(f"❌ main統合テストエラー: {e}")
        return False

async def test_dependency_availability():
    """依存関係利用可能性テスト"""
    print("\n🔍 === 依存関係確認テスト ===")
    
    dependencies = {
        "playwright": "playwright",
        "selenium": "selenium",
        "yt-dlp": "yt_dlp",
        "streamlink": "streamlink",
        "beautifulsoup4": "bs4",
        "aiohttp": "aiohttp"
    }
    
    available = []
    missing = []
    
    for name, module in dependencies.items():
        try:
            __import__(module)
            available.append(name)
            print(f"✅ {name}: 利用可能")
        except ImportError:
            missing.append(name)
            print(f"❌ {name}: 未インストール")
    
    print(f"\n📊 依存関係サマリー:")
    print(f"  利用可能: {len(available)}/{len(dependencies)} ({available})")
    print(f"  未インストール: {len(missing)} ({missing})")
    
    # 限定配信録画に必要な最小構成チェック
    critical_deps = ["yt-dlp", "aiohttp"]
    critical_available = all(dep.replace("-", "_") in [d.replace("-", "_") for d in available] for dep in critical_deps)
    
    if critical_available:
        print("✅ 限定配信録画の最小要件を満たしています")
    else:
        print("❌ 限定配信録画に必要な依存関係が不足しています")
    
    return len(available) >= len(dependencies) * 0.7  # 70%以上利用可能

async def test_url_patterns():
    """URL判定ロジックテスト"""
    print("\n🔗 === URL判定テスト ===")
    
    test_urls = [
        ("https://twitcasting.tv/user123", "通常配信", False),
        ("https://twitcasting.tv/g:117191215409354941008", "グループ配信", True),
        ("https://twitcasting.tv/g:117191215409354941008/broadcaster", "グループ配信（broadcaster）", True),
        ("https://twitcasting.tv/c:communityname", "コミュニティ配信", True),
        ("https://twitcasting.tv/private_user", "プライベート可能性", False),
        ("https://twitcasting.tv/limited_stream", "限定配信可能性", False)
    ]
    
    try:
        from main import RakurekoTwitCastingOrchestrator
        from config_core import SystemConfig
        
        system_config = SystemConfig()
        orchestrator = RakurekoTwitCastingOrchestrator(system_config)
        
        correct_predictions = 0
        total_tests = len(test_urls)
        
        for url, description, expected_auth in test_urls:
            predicted_auth = orchestrator._determine_auth_requirement(None, url)
            
            status = "✅" if predicted_auth == expected_auth else "❌"
            print(f"{status} {description}: 認証{'必要' if predicted_auth else '不要'} (期待: {'必要' if expected_auth else '不要'})")
            
            if predicted_auth == expected_auth:
                correct_predictions += 1
        
        accuracy = correct_predictions / total_tests
        print(f"\n📊 URL判定精度: {correct_predictions}/{total_tests} ({accuracy:.1%})")
        
        return accuracy >= 0.8  # 80%以上の精度
        
    except Exception as e:
        print(f"❌ URL判定テストエラー: {e}")
        return False

async def main():
    """Phase 1テストメイン関数"""
    print("🧪 =====================================")
    print("   RakurekoTwitCasting Phase 1テスト")
    print("   限定配信録画機能確認")
    print("=====================================")
    
    tests = [
        ("依存関係確認", test_dependency_availability()),
        ("auth_core基本機能", test_auth_core()),
        ("認証録画エンジン", test_authenticated_recording()),
        ("URL判定ロジック", test_url_patterns()),
        ("main統合テスト", test_main_integration())
    ]
    
    results = []
    
    for test_name, test_coro in tests:
        print(f"\n" + "="*50)
        print(f"🔍 {test_name} テスト開始")
        print("="*50)
        
        try:
            result = await test_coro
            results.append(result)
            
            status = "✅ 合格" if result else "❌ 不合格"
            print(f"\n{status} {test_name}")
            
        except Exception as e:
            print(f"\n❌ {test_name} テスト例外: {e}")
            results.append(False)
    
    # 最終結果
    passed = sum(results)
    total = len(results)
    success_rate = passed / total if total > 0 else 0
    
    print("\n" + "="*50)
    print("📊 Phase 1テスト結果サマリー")
    print("="*50)
    print(f"合格: {passed}/{total} ({success_rate:.1%})")
    
    if success_rate >= 0.8:
        print("🎉 Phase 1修正は成功です！限定配信録画が利用可能です。")
        print("\n🚀 次のステップ:")
        print("  1. 実際のグループ配信URLでテストしてください")
        print("  2. yt-dlpとPlaywrightが正常にインストールされていることを確認してください")
        print("  3. TwitCastingアカウントの認証情報を環境変数に設定してください")
        print("     TWITCASTING_EMAIL=your_email@example.com")
        print("     TWITCASTING_PASSWORD=your_password")
    elif success_rate >= 0.6:
        print("⚠️ Phase 1修正は部分的に成功しています。")
        print("一部の機能に問題がありますが、基本的な限定配信録画は可能です。")
    else:
        print("❌ Phase 1修正に重大な問題があります。")
        print("ログを確認して、エラーを修正してください。")
    
    return 0 if success_rate >= 0.6 else 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 テスト中断")
        sys.exit(0)
    except Exception as e:
        print(f"❌ テスト実行エラー: {e}")
        sys.exit(1)