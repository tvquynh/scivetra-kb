# Bắt đầu từ đây

Bộ hướng dẫn này dành cho người **không chuyên IT**. Bạn chỉ cần biết bấm chuột, sao chép (Ctrl+C) và dán (Ctrl+V). Mỗi bước đều có ô lệnh để sao chép nguyên văn, và có mô tả kết quả bạn sẽ thấy nếu mọi thứ đúng.

## SciVetra KB là gì?

SciVetra KB là một **kho tri thức nghiên cứu chạy trên máy của bạn** (không cần Internet, không gửi dữ liệu đi đâu). Kho giúp người làm tổng quan tài liệu khoa học:

- **lưu và kiểm tra thông tin bài báo:** DOI, tác giả, bài bị rút hay được đính chính;
- **quản lý file PDF:** cùng quyền sử dụng của từng file (được lưu, được trích, được chia sẻ hay không);
- **đọc nội dung PDF an toàn:** trong một "hộp cách ly";
- **ghi lại bằng chứng khoa học:** kết quả, số liệu, hạn chế, kèm vị trí chính xác trong bài;
- **tìm kiếm:** trong các bài đã có quyền;
- **lập bản nháp tổng hợp:** mọi nhận định đều được dẫn nguồn, hoặc được đánh dấu rõ là "chưa kiểm chứng".

**Nguyên tắc vàng:** máy chỉ *đề xuất*; **con người quyết định**. Mọi thay đổi đều được ghi lại, không thể xóa lén.

*English version: [`../guide/`](../guide/00-START-HERE.md). Hướng dẫn cho macOS và Linux có trong bản tiếng Anh.*

## Làm theo thứ tự này

| Bước | Tài liệu | Thời gian |
|---|---|---|
| 1 | [Cài Python](01-cai-dat-python.md) | 10 phút |
| 2 | [PowerShell và cài đặt bảo mật (đọc nếu gặp lỗi đỏ)](02-powershell-va-bao-mat.md) | 5 phút |
| 3 | [Tải SciVetra và cài đặt](03-tai-va-cai-dat-scivetra.md) | 10 phút |
| 4 | [Chạy thử với dữ liệu mẫu](05-chay-thu.md) | 10 phút |
| 5 | [Lần đầu sử dụng: cửa sổ làm việc và cách đọc kết quả](04-lan-dau-su-dung.md) | 10 phút |
| 6 | [Sử dụng hằng ngày (các lệnh để sao chép)](06-su-dung-hang-ngay.md) | khi cần |
| 7 | [Sao lưu, khôi phục, nâng cấp](07-sao-luu-khoi-phuc-nang-cap.md) | khi cần |
| 8 | [Xử lý lỗi thường gặp](08-xu-ly-loi.md) | khi cần |
| 9 | [Từ điển thuật ngữ](09-tu-dien-thuat-ngu.md) | khi cần |
| 10 | [Giới hạn và an toàn](10-gioi-han-va-an-toan.md) | nên đọc một lần |
| 11 | [Dùng LLM miễn phí và mô hình trên máy (giai đoạn L1–L2)](12-llm-mien-phi.md) | tùy chọn |

## Nhanh nhất (nếu đã có Python)

1. Tải SciVetra về và giải nén vào `C:\SciVetra\scivetra-kb` ([bước 3](03-tai-va-cai-dat-scivetra.md)).
2. Bấm đúp **`1-INSTALL.cmd`**.
3. Bấm đúp **`2-TRY-DEMO.cmd`**.
4. Bấm đúp **`3-OPEN-WORK-WINDOW.cmd`** mỗi khi muốn làm việc.

## Thư mục trên máy bạn sau khi cài

```
C:\SciVetra\
  scivetra-kb\   <- mã nguồn bạn tải về (KHÔNG sửa gì trong này)
  env\           <- "môi trường ảo" Python riêng của SciVetra (do bước cài tạo ra)
  data\          <- các kho dữ liệu (KB) của bạn
  backups\       <- các bản sao lưu
  output\        <- file xuất ra (bản nháp, báo cáo)
  packages\      <- bộ đọc PDF đã tải về
```
