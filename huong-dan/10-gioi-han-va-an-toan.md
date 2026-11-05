# Giới hạn và an toàn — nên đọc một lần

## SciVetra **luôn** làm

- Chạy **trên máy bạn**, không gửi dữ liệu lên mạng.
- **Kiểm tra quyền sử dụng ở mỗi lần dùng.** Khi quyền thay đổi, lần dùng sau thấy ngay.
- **Ghi lại mọi việc** (ai, khi nào, làm gì) và **không cho sửa hay xóa lén** lịch sử.
- Coi kết quả của máy là **đề xuất**; con người mới quyết định.

## Phiên bản này **chưa** làm (cố ý)

| Chưa có | Lý do / cần gì |
|---|---|
| AI trực tuyến (ChatGPT, Claude, Groq, Gemini…) | SciVetra không gọi. Phần sàng lọc (S07) dùng **máy giả lập**. Chỉ **mô hình chạy trên máy bạn** (Ollama / LM Studio) được *đề xuất* bằng chứng ([hướng dẫn 12](12-llm-mien-phi.md)). AI trực tuyến cần chủ dự án duyệt (OAG-6). |
| Tìm kiếm "hiểu nghĩa" | Tìm theo từ; phần "embedding" có sẵn chỉ là bản thay thế đơn giản, **không** phải AI hiểu nghĩa |
| Tra cứu DOI trực tuyến (Crossref, DataCite) | Chưa bật mạng; cần chủ dự án duyệt (OAG-3) |
| Đọc PDF scan (OCR) | Chưa hỗ trợ |
| Chia sẻ bản nháp hoặc bằng chứng ra ngoài | Luôn bị từ chối; cần chính sách chia sẻ (OAG-7) |
| Mã hóa dữ liệu trên ổ đĩa | Hãy bật **BitLocker** cho ổ chứa `C:\SciVetra` nếu dữ liệu nhạy cảm |
| Xác thực danh tính người dùng | Tên người duyệt do người dùng tự khai |

## Lưu ý với PDF thật trên Windows

Bộ đọc PDF chạy trong hộp cách ly, nhưng trên Windows hộp này **chưa mạnh bằng trên Linux**. Vì vậy:
- **chỉ đọc PDF từ nguồn bạn tin tưởng**, như nhà xuất bản hay kho lưu trữ chính thức;
- PDF lạ hoặc có thể độc hại nên được xử lý trên Linux.

**macOS:** hiện **chưa hỗ trợ đọc PDF** — hộp cách ly không khởi động được trên macOS nên mọi PDF đều bị từ chối (an toàn nhưng không đọc được). Hãy dùng Windows hoặc Linux; các chức năng khác cũng chưa được kiểm chứng riêng trên macOS.

## Dữ liệu thật của dự án

Thư mục `E:\ResearchKB` được dành riêng cho dữ liệu thật và **bị khóa** cho đến khi chủ dự án chọn nơi lưu và nơi sao lưu (OAG-1, OAG-2). Trong lúc chờ, hãy làm việc trong `C:\SciVetra\data`.

## Trạng thái phiên bản

- **Hãy dùng bản 1.0.1 trở lên.** Bản 1.0.0 bị thiếu một phần chương trình (bằng chứng, S08) nên khi tải mới thì các chức năng bằng chứng, tìm kiếm, tổng hợp và AI trích xuất bị lỗi. Bản 1.0.1 sửa lỗi này và kiểm tra chính gói đã cài.
- **Chức năng nào chạy được và đã thử thế nào:** xem `docs/CAPABILITY_MATRIX.md`. Chữ "verified" ở đó nghĩa là *tìm thấy con số và câu trích trong bài*, không có nghĩa kết luận khoa học là đúng.
- Phần tổng hợp (S11) **đang chờ kiến trúc sư độc lập duyệt**. Nếu có sửa đổi, sẽ có bản cập nhật 1.0.x.
- **Mô hình AI trên máy chỉ *đề xuất* bằng chứng.** Trên máy thử nghiệm, mô hình 7B tìm được khoảng một nửa số bản ghi mong đợi và có sai sót; mọi sai sót đều bị SciVetra chặn ở bước kiểm tra hoặc để lại cho người duyệt.
- Chi tiết kỹ thuật (tiếng Anh): `docs/CAPABILITY_MATRIX.md` và `docs/SECURITY_REVIEW_S12.md`.
