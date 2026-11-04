# Bước 2 — PowerShell và cài đặt bảo mật

## Có cần "tắt bảo mật PowerShell" không?

**Thường là KHÔNG.** Ba file bấm đúp của SciVetra (`1-INSTALL.cmd`, `2-TRY-DEMO.cmd`, `3-OPEN-WORK-WINDOW.cmd`) tự xin phép chạy script **chỉ cho riêng lần chạy đó**. Cài đặt bảo mật của máy giữ nguyên.

Bạn chỉ cần mục 2.2 nếu:
- muốn tự chạy các file `.ps1` bằng tay; hoặc
- thấy lỗi đỏ có chữ **"running scripts is disabled on this system"**.

## 2.1 Windows chặn file tải từ Internet

Khi bấm đúp file `.cmd` lần đầu, Windows có thể hiện khung xanh **"Windows protected your PC"**. Có hai cách xử lý:

- **Cách 1 (một lần cho tất cả):** *trước khi giải nén*, chuột phải vào file ZIP đã tải → **Properties** → đánh dấu ☑ **Unblock** (Bỏ chặn) → **OK**. Sau đó mới giải nén.
- **Cách 2 (từng file):** trong khung xanh, bấm **More info** (Thông tin thêm) → **Run anyway** (Vẫn chạy).

Chỉ làm vậy với file SciVetra tải từ trang chính thức `github.com/tvquynh/scivetra-kb`.

## 2.2 Cho phép chạy script PowerShell (chỉ khi cần)

Mở PowerShell **bình thường** (không cần quyền quản trị), dán lệnh sau rồi bấm **Enter**:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Nếu được hỏi, gõ `Y` rồi bấm **Enter**.

- **Ý nghĩa:** chỉ tài khoản của bạn được chạy script tạo trên máy, cùng script tải về đã được bỏ chặn (mục 2.1). Đây là mức an toàn Microsoft khuyên dùng. Nó **không** tắt bảo mật của toàn máy.
- **Kiểm tra:** chạy `Get-ExecutionPolicy -Scope CurrentUser`; kết quả phải là `RemoteSigned`.
- **Trả lại như cũ:** chạy lệnh sau.

  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Undefined
  ```

**Không** dùng `Unrestricted` hay `Bypass` cho cả máy; SciVetra không cần mức đó.

## 2.3 Mẹo dùng PowerShell

| Việc | Cách làm |
|---|---|
| Dán lệnh | Chuột phải vào cửa sổ, hoặc Ctrl+V |
| Chạy lệnh | Bấm **Enter** |
| Lặp lại lệnh trước | Phím mũi tên lên |
| Dừng lệnh đang chạy | Ctrl+C |
| Lệnh dài nhiều dòng | Dán cả khối rồi bấm Enter |
