import os
import json
import sys

# 프로젝트 루트 추가
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    notifications_path = os.path.join(root_dir, ".agents", "drift_notifications.json")
    
    if not os.path.exists(notifications_path):
        print("CHECK_RESULT: NO_NOTIFICATIONS")
        return

    try:
        with open(notifications_path, 'r', encoding='utf-8') as f:
            notifications = json.load(f)
    except Exception as e:
        print(f"Error loading notifications: {e}")
        return

    if not notifications:
        print("CHECK_RESULT: NO_NOTIFICATIONS")
        return

    print("CHECK_RESULT: NOTIFICATIONS_FOUND")
    print(f"\n[!] 지식베이스 노화(Drift) 감지 내역 ({len(notifications)}건):")
    print("| 번호 | 대상 노트 | 관심사 범주 | 감지 일시 | 임시 초안 경로 |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for idx, n in enumerate(notifications, 1):
        print(f"| {idx} | {n['file_path']} | {n['category']} | {n['detected_at'][:19]} | {n['draft_path']} |")

if __name__ == "__main__":
    main()
