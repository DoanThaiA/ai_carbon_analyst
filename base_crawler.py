from abc import ABC, abstractmethod


class BaseCrawler(ABC):
    """
    Lớp cha mà MỖI crawler theo từng trang web phải kế thừa.
    """

    name: str = "base"         
    source_type: str = "web"

    @abstractmethod
    def run(self) -> dict:
        """
        Thực hiện crawl + lưu dữ liệu (raw + bảng có cấu trúc).
        Phải tự bắt lỗi bên trong, KHÔNG được để exception văng ra ngoài
        làm dừng cả bộ điều phối tổng.

        Trả về dict thống kê, ví dụ:
            {"news_saved": 5, "errors": []}
            {"prices_saved": 2, "errors": ["RWTC: timeout"]}
        """
        raise NotImplementedError
