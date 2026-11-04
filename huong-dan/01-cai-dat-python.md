# Bước 1 — Cài Python

SciVetra được viết bằng ngôn ngữ **Python**. Máy cần có Python **3.11 trở lên**. Bản đã được kiểm tra là 3.11 và 3.14.

## 1.1 Kiểm tra máy đã có Python chưa

1. Bấm phím **Windows**, gõ `powershell`, rồi bấm **Enter**. Một cửa sổ nền xanh hoặc đen hiện ra; đó là **PowerShell**.
2. Sao chép dòng dưới, dán vào cửa sổ (chuột phải hoặc Ctrl+V), rồi bấm **Enter**:

   ```powershell
   py --version
   ```

| Bạn thấy | Nghĩa là | Làm gì |
|---|---|---|
| `Python 3.11.x`, `3.12.x`, `3.13.x` hoặc `3.14.x` | Đã có Python phù hợp | Bỏ qua bước 1, sang [bước 3](03-tai-va-cai-dat-scivetra.md) |
| Số nhỏ hơn 3.11, hoặc chữ đỏ báo `py` không được nhận ra | Chưa có, hoặc quá cũ | Làm tiếp mục 1.2 |

## 1.2 Tải và cài Python

1. Mở trình duyệt, vào **https://www.python.org/downloads/windows/**.
2. Bấm nút tải bản Python **mới nhất** dành cho Windows. Chọn **Windows installer (64-bit)**.
3. Mở file vừa tải (thường nằm trong thư mục *Downloads*).
4. **QUAN TRỌNG:** ở màn hình đầu tiên, đánh dấu vào ô:
   - ☑ **Add python.exe to PATH**
   - ☑ **Use admin privileges when installing py.exe** (nếu có)
5. Bấm **Install Now** và chờ đến khi thấy **Setup was successful**, rồi bấm **Close**.

> Nếu python.org chỉ còn cung cấp **Python install manager**: tải và cài nó, rồi mở PowerShell và chạy `py install 3.14`.

## 1.3 Kiểm tra lại

**Đóng hết** các cửa sổ PowerShell cũ, mở một cửa sổ mới, rồi chạy:

```powershell
py --version
```

Kết quả phải là Python **3.11 trở lên**, ví dụ `Python 3.14.0`. Xong bước 1.

**Nếu vẫn báo lỗi:**
- Khởi động lại máy, rồi thử lại.
- Nếu vẫn lỗi, xem [Xử lý lỗi, mục 1](08-xu-ly-loi.md#1-py-hoặc-python-không-được-nhận-ra).
