#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
process_manager.py - 高機能版復元
録画テスト完了まで対応
"""

import asyncio
import psutil
import logging
import time
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class ProcessManager:
    """高機能プロセス管理クラス"""
    
    def __init__(self):
        self.system_type = "windows"
        logger.info("ProcessManager初期化完了")
    
    async def terminate_orphan_processes(self, patterns: List[str]) -> Dict[str, int]:
        """段階的プロセス終了（terminate → kill）"""
        result = {}
        
        for pattern in patterns:
            count = 0
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    proc_name = proc.info['name'].lower()
                    if pattern.lower() in proc_name:
                        pid = proc.info['pid']
                        
                        # Step 1: 穏やかな終了
                        logger.info(f"プロセス終了開始: {proc_name} (PID: {pid})")
                        proc.terminate()
                        
                        try:
                            proc.wait(timeout=3)
                            logger.info(f"✅ 穏やかな終了成功: {proc_name}")
                        except psutil.TimeoutExpired:
                            # Step 2: 強制終了
                            logger.warning(f"強制終了実行: {proc_name}")
                            proc.kill()
                            proc.wait(timeout=2)
                            logger.info(f"✅ 強制終了完了: {proc_name}")
                        
                        count += 1
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                except Exception as e:
                    logger.error(f"プロセス終了エラー: {e}")
                    continue
            
            result[pattern] = count
            logger.info(f"パターン '{pattern}': {count}個のプロセス終了")
        
        return result
    
    async def move_files_safely(self, temp_dir: Path, completed_dir: Path) -> List[Dict[str, Any]]:
        """安全なファイル移動（temp → completed）"""
        moved_files = []
        
        if not temp_dir.exists():
            logger.warning(f"temp ディレクトリが存在しません: {temp_dir}")
            return moved_files
        
        # completed ディレクトリ作成
        completed_dir.mkdir(parents=True, exist_ok=True)
        
        # .mp4 ファイルを移動
        for mp4_file in temp_dir.glob("*.mp4"):
            try:
                dest_file = completed_dir / mp4_file.name
                
                # ファイル移動実行
                mp4_file.replace(dest_file)
                
                # 移動確認
                if dest_file.exists() and not mp4_file.exists():
                    file_size = dest_file.stat().st_size
                    moved_files.append({
                        'src': str(mp4_file),
                        'dest': str(dest_file),
                        'size_mb': round(file_size / (1024*1024), 2)
                    })
                    logger.info(f"✅ ファイル移動成功: {dest_file.name} ({file_size/1024/1024:.1f}MB)")
                else:
                    logger.error(f"❌ ファイル移動失敗: {mp4_file.name}")
                    
            except Exception as e:
                logger.error(f"ファイル移動エラー: {mp4_file.name} - {e}")
        
        return moved_files
    
    async def cleanup_recording_session(self, temp_dir: Path, completed_dir: Path) -> Dict[str, Any]:
        """録画セッション完了後の統合クリーンアップ"""
        logger.info("🧹 録画セッションクリーンアップ開始")
        
        result = {
            'processes_terminated': {},
            'files_moved': [],
            'errors': [],
            'success': False,
            'start_time': datetime.now()
        }
        
        try:
            # Step 1: プロセス終了
            logger.info("Step 1: 孤児プロセス終了")
            result['processes_terminated'] = await self.terminate_orphan_processes(['yt-dlp', 'ffmpeg'])
            
            # プロセス終了後の待機
            await asyncio.sleep(2)
            
            # Step 2: ファイル移動
            logger.info("Step 2: ファイル移動実行")
            result['files_moved'] = await self.move_files_safely(temp_dir, completed_dir)
            
            # 成功判定
            total_processes = sum(result['processes_terminated'].values())
            total_files = len(result['files_moved'])
            
            result['success'] = True
            result['end_time'] = datetime.now()
            
            # 結果ログ
            logger.info(f"✅ クリーンアップ完了: プロセス{total_processes}個終了, ファイル{total_files}個移動")
            
            return result
            
        except Exception as e:
            logger.error(f"クリーンアップエラー: {e}")
            result['errors'].append(f"クリーンアップ例外: {e}")
            result['success'] = False
            return result

# テスト用関数
async def test_cleanup():
    """クリーンアップテスト"""
    pm = ProcessManager()
    temp_dir = Path("recordings/temp")
    completed_dir = Path("recordings/completed") 
    
    result = await pm.cleanup_recording_session(temp_dir, completed_dir)
    print(f"テスト結果: {result}")

if __name__ == "__main__":
    print("🧪 ProcessManager高機能版テスト")
    asyncio.run(test_cleanup())