#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recording_options.py - 録画オプションとデータクラス定義（修正版）
統一的な録画設定管理・セキュリティ強化
"""

from typing import Optional
from dataclasses import dataclass, field

@dataclass
class RecordingOptions:
    """録画オプション（セキュリティ強化版）"""
    confirmed_by_user: bool = False
    password: Optional[str] = field(default=None, repr=False)  # ログ出力抑制
    headless: bool = False
    timeout_minutes: int = 180
    quality: str = "best"
    session_name: Optional[str] = None  # 呼び出し側で設定
    auto_retry: bool = True
    max_retries: int = 3
    retry_base_delay: int = 5  # リトライ間隔（秒）
    
    def __post_init__(self):
        """バリデーション処理"""
        # セッション名自動生成は削除（呼び出し側で管理）
        
        # 値チェック
        if self.timeout_minutes < 1:
            raise ValueError("timeout_minutes must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.retry_base_delay < 1:
            raise ValueError("retry_base_delay must be >= 1")
        
        # quality正規化（不正値はbestにフォールバック）
        valid_qualities = ["best", "worst", "hd", "medium", "low"]
        if self.quality not in valid_qualities:
            self.quality = "best"
    
    def __repr__(self):
        """パスワードマスク表示"""
        fields = []
        for field_name, field_value in self.__dict__.items():
            if field_name == 'password' and field_value:
                fields.append(f"{field_name}='***'")
            else:
                fields.append(f"{field_name}={repr(field_value)}")
        return f"RecordingOptions({', '.join(fields)})"