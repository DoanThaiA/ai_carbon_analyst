"""
Insert 1 lần dữ liệu release note ban đầu (16 mục QA báo cáo 16/09) vào bảng
release_notes. Idempotent: nếu bảng đã có dữ liệu thì bỏ qua, trừ khi truyền
--force (khi đó vẫn insert thêm, không xoá dữ liệu cũ).

Dùng:
    python -m scripts.seed_release_notes
    python -m scripts.seed_release_notes --force
"""
import argparse
import asyncio

from sqlalchemy import func, select

from core.config import Settings
from db.models import ReleaseNote
from db.session import build_sessionmaker, create_engine

# (customer_request, change_description, test_result, status)
_SEED_ROWS = [
    (
        'Căn chỉnh lại bố cục trình bày: thu nhỏ khung "Tổng hợp AI · Nguồn tier A/B/C đã kiểm chứng", căn lề header.',
        "Đã loại bỏ phần banner do trùng thông tin với header, chỉ giữ lại phần thời gian và thanh trượt giá.",
        "Banner cũ (logo lớn + badge) đã được thay bằng thanh ngày + thanh giá chạy ngang gọn gàng. Khung badge đã bỏ hẳn thay vì chỉ thu nhỏ — đã trao đổi và khách hàng xác nhận chấp nhận phương án này để tránh trùng lặp với header.",
        "dat",
    ),
    (
        'Mục "Tóm tắt điều hành" bị lỗi hiển thị (trắng/hỏng giao diện).',
        'Đã đổi thành mục "Điểm nhấn" và hiển thị giao diện đẹp.',
        'Mục "🚨 ĐIỂM NHẤN" hiển thị đúng, có gắn tag theo chủ đề (EUA, Năng lượng, Địa chính trị, Vĩ mô, Chính sách...), không còn lỗi.',
        "dat",
    ),
    (
        "Lỗi trắng trang tại khu vực đầu Phần 1.",
        "Đã cập nhật cân đối sang trang cho phù hợp.",
        'Khu vực "Phần 1: Tổng quan giá thị trường" hiển thị ổn định, bố cục 2 cột (biểu đồ nến + số liệu chính), không còn khoảng trắng bất thường.',
        "dat",
    ),
    (
        "Căn chỉnh lại cột/dòng Bảng giá nhanh; đặt nguồn (link) embedded vào chữ; sửa đơn vị CBAM Certificate thành EUR/tCO2; căn chỉnh cột ghi chú, cách dòng phù hợp.",
        "Đã đặt embedded vào chữ, sửa đơn vị CBAM, loại bỏ cột Δ ngày/Δ tuần riêng, mở rộng cột ghi chú.",
        'Đã kiểm tra trực tiếp: tên hợp đồng (vd "TTF Dutch Natural Gas") là hyperlink thật trỏ tới nguồn Barchart; CBAM Certificate hiển thị đúng "75.28 EUR/tCO2"; bảng chỉ còn 3 cột (Hợp đồng / Giá / Ghi chú), Δ ngày–Δ tuần gộp vào ô Giá, cột Ghi chú đã mở rộng.',
        "dat",
    ),
    (
        "Bổ sung cửa sổ nổi (hover tooltip): khi di chuột vào sẽ hiện cửa sổ nổi xem chi tiết/biểu đồ từ nguồn gốc.",
        '(Không có ghi nhận "đã sửa" cho mục này trong file gốc.)',
        "Đã thử hover trực tiếp vào tên hợp đồng và giá trong Bảng giá nhanh — không có cửa sổ nổi nào xuất hiện, chỉ có liên kết thường dẫn ra trang nguồn khi click.",
        "chua_dat",
    ),
    (
        "Bổ sung giá trị tuyệt đối thay vì chỉ hiển thị tỷ lệ %.",
        "Đã bổ sung.",
        'Đã thấy hiển thị đầy đủ dạng "Δ Tuần: -0,97% (-0,83)" — có cả % và giá trị tuyệt đối.',
        "dat",
    ),
    (
        'Gộp nội dung "Diễn biến chính" vào ô ghi chú của hợp đồng liên quan trong bảng giá; xóa mục "Diễn biến chính" riêng.',
        "Đã thay đổi: xóa phần Diễn biến chính, các diễn biến được ghi vào ô ghi chú của hợp đồng bị tác động.",
        'Không còn mục "Diễn biến chính" độc lập trong báo cáo; nội dung diễn biến (vd Hormuz, Cameron LNG, Salzgitter...) đã nằm trong ô Ghi chú của từng hợp đồng tương ứng.',
        "dat",
    ),
    (
        'Đưa kết luận lên ngay headline của từng nhóm phân tích (vd "Năng lượng & nhiên liệu hoá thạch"), đóng khung & highlight; áp dụng tương tự cho các nhóm khác.',
        "Đã thay đổi đưa kết luận lên với headline.",
        'Đã kiểm chứng nhiều nhóm ("Năng lượng & nhiên liệu hoá thạch", "Hạn ngạch & tín chỉ carbon"...) đều có câu kết luận đưa lên đầu, đóng khung màu xanh nổi bật trước phần diễn giải chi tiết.',
        "dat",
    ),
    (
        'Đồng bộ toàn bộ nội dung "Cần theo dõi" với "Tin tức cần theo dõi" ở phần dưới báo cáo.',
        "Đã thay đổi đưa phần cần theo dõi xuống phần Tin tức theo dõi 7 ngày tới ở phía dưới.",
        'Khối "CẦN THEO DÕI" (7 điểm theo dõi chi tiết) nằm ngay dưới khối "Lịch sự kiện 7 ngày tới", cùng một khu vực của báo cáo — đúng như ghi nhận và đã được khách hàng xác nhận là đạt yêu cầu.',
        "dat",
    ),
    (
        'Đưa "Động lực thị trường" lên đầu, trước phần Phân tích; bỏ lặp lại số liệu bảng giá; viết tóm tắt ngắn gọn, định tính về động lực tăng/giảm.',
        "Đã thay đổi viết nội dung tóm lược không lặp lại về dữ liệu giá.",
        '"Động lực thị trường" đã nằm trước mục "Phân tích"; nội dung viết định tính (vd "EUA vẫn giữ mặt bằng cao hơn đáng kể...") thay vì liệt kê lại số liệu giá thô.',
        "dat",
    ),
    (
        'Chuyển "Kịch bản chiến lược" thành dạng bảng.',
        "Đã đưa thành bảng.",
        '"Kịch bản chiến lược" hiển thị dạng bảng 3 cột (Ngắn hạn / Trung hạn / Dài hạn) x 5 hàng chỉ tiêu (xác suất, điều kiện kích hoạt, vùng giá, rủi ro, chiến lược trading).',
        "dat",
    ),
    (
        "Đưa mục cập nhật tín chỉ carbon & CBAM xuống dưới phần tóm tắt thông tin chính; gắn tag cho từng tin tức (vd tin CBAM có tag CBAM).",
        "Đã gắn tags cho phần tóm tắt thông tin chính với mỗi tin tức.",
        'Mục "Cập nhật tín chỉ carbon & CBAM" đã nằm phía dưới khối Điểm nhấn; các tin tức ở Phần 3 đều có gắn tag chủ đề (DẦU, ĐỊA CHÍNH TRỊ, KHÍ GAS, VCM, EUA/ETS...) cạnh tiêu đề.',
        "dat",
    ),
    (
        '"Tín hiệu liên thị trường" đang lặp với phần Phân tích; đưa "Chính sách/MSR" vào phần Phân tích; thống nhất nguyên tắc phân tích, không tách rời.',
        "Đã thay đổi gộp phần Tín hiệu liên thị trường vào phần phân tích.",
        'Không còn mục "Tín hiệu liên thị trường" riêng; nội dung (Fuel switching, Dầu, Gasoil/crack spread, Hormuz, Than, Chính sách/MSR) đã nằm trong phần "Phân tích" duy nhất.',
        "dat",
    ),
    (
        '"Quan điểm trái chiều đáng chú ý" đang lặp nội dung với phần Phân tích.',
        "Đã thay đổi gộp phần Quan điểm thị trường vào phần phân tích.",
        'Nội dung quan điểm trái chiều/đồng thuận đã thành mục con "QUAN ĐIỂM THỊ TRƯỜNG" nằm trong phần Phân tích, không còn là mục riêng bị lặp.',
        "dat",
    ),
    (
        'Cần bổ sung lịch sự kiện thuộc phần "Cần theo dõi".',
        "Đã bổ sung.",
        '"Cần theo dõi" đã được đưa vào khu vực Lịch sự kiện 7 ngày tới, đúng như xác nhận của khách hàng về vị trí/đồng bộ nội dung.',
        "dat",
    ),
    (
        "Căn chỉnh lại bảng biểu, giãn dòng... cho gọn gàng.",
        "Đã căn chỉnh lại kích thước các cột bảng biểu và giãn dòng.",
        "Các bảng (Bảng giá nhanh, Bảng tín hiệu nhanh, Kịch bản chiến lược, Gợi ý kinh doanh & giải pháp) đều có cột/dòng canh đều, giãn dòng hợp lý, dễ đọc.",
        "dat",
    ),
]


async def main(force: bool) -> None:
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)

    async with session_factory() as session:
        existing = (await session.execute(select(func.count(ReleaseNote.id)))).scalar_one()
        if existing and not force:
            print(f"release_notes đã có {existing} dòng — bỏ qua (dùng --force nếu vẫn muốn thêm).")
            return

        next_order = (
            await session.execute(select(func.coalesce(func.max(ReleaseNote.order_index), 0)))
        ).scalar_one()

        rows = [
            ReleaseNote(
                order_index=next_order + i,
                customer_request=customer_request,
                change_description=change_description,
                test_result=test_result,
                status=status,
            )
            for i, (customer_request, change_description, test_result, status) in enumerate(
                _SEED_ROWS, start=1
            )
        ]
        session.add_all(rows)
        await session.commit()
        print(f"Đã insert {len(rows)} dòng vào release_notes.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Vẫn insert dù bảng đã có dữ liệu")
    args = parser.parse_args()
    asyncio.run(main(args.force))
