"""
Sinh bcrypt hash cho mật khẩu admin — dán kết quả vào ADMIN_PASSWORD_HASH
trong .env. Không ghi gì vào DB, chỉ in ra hash để copy thủ công.

Dùng:
    python -m scripts.generate_admin_hash
"""
import getpass

from core.security import hash_password


def main() -> None:
    password = getpass.getpass("Nhập mật khẩu admin muốn set: ")
    confirm = getpass.getpass("Nhập lại để xác nhận: ")
    if password != confirm:
        print("Hai lần nhập không khớp, thử lại.")
        return
    if not password:
        print("Mật khẩu không được để trống.")
        return

    print("\nDán dòng sau vào .env:\n")
    print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")


if __name__ == "__main__":
    main()
