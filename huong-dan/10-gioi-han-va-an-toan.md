# Giới hạn và an toàn — nên đọc một lần

## SciVetra **luôn** làm

- Chạy **trên máy bạn**, không gửi dữ liệu lên mạng.
- **Kiểm tra quyền sử dụng ở mỗi lần dùng.** Khi quyền thay đổi, lần dùng sau thấy ngay.
- **Ghi lại mọi việc** (ai, khi nào, làm gì) và **không cho sửa hay xóa lén** lịch sử.
- Coi kết quả của máy là **đề xuất**; con người mới quyết định.

## Phiên bản này **chưa** làm (cố ý)

| Chưa có | Lý do / cần gì |
|---|---|
| AI thật (ChatGPT, Claude, mô hình cục bộ) | Mọi phần "AI" đang dùng **máy giả lập**. Bật AI thật cần chủ dự án duyệt (OAG-6). |
| Tra cứu DOI trực tuyến (Crossref, DataCite) | Chưa bật mạng; cần chủ dự án duyệt (OAG-3) |
| Đọc PDF scan (OCR) | Chưa hỗ trợ |
| Chia sẻ bản nháp hoặc bằng chứng ra ngoài | Luôn bị từ chối; cần chính sách chia sẻ (OAG-7) |
| Mã hóa dữ liệu trên ổ đĩa | Hãy bật **BitLocker** cho ổ chứa `C:\SciVetra` nếu dữ liệu nhạy cảm |
| Xác thực danh tính người dùng | Tên người duyệt do người dùng tự khai |

## Lưu ý với PDF thật trên Windows

Bộ đọc PDF chạy trong hộp cách ly, nhưng trên Windows hộp này **chưa mạnh bằng trên Linux**. Vì vậy:
- **chỉ đọc PDF từ nguồn bạn tin tưởng**, như nhà xuất bản hay kho lưu trữ chính thức;
- PDF lạ hoặc có thể độc hại nên được xử lý trên Linux.

## Dữ liệu thật của dự án

Thư mục `E:\ResearchKB` được dành riêng cho dữ liệu thật và **bị khóa** cho đến khi chủ dự án chọn nơi lưu và nơi sao lưu (OAG-1, OAG-2). Trong lúc chờ, hãy làm việc trong `C:\SciVetra\data`.

## Trạng thái phiên bản

- **1.0.0rc1 là "bản ứng viên phát hành"** đã qua toàn bộ kiểm tra tự động: 641 bài kiểm tra trên Python 3.11 và 3.14, cùng 8 cổng kiểm tra phát hành.
- Phần tổng hợp (S11) **đang chờ kiến trúc sư độc lập duyệt**. Nếu có sửa đổi, sẽ có bản 1.0.0rc2.
- Chi tiết kỹ thuật (tiếng Anh): `docs/CAPABILITY_MATRIX.md` và `docs/SECURITY_REVIEW_S12.md`.
