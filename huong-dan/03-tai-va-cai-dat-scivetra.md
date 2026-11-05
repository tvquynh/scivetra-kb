# Bước 3 — Tải SciVetra và cài đặt

## 3.1 Tải về

1. Mở trình duyệt, vào **https://github.com/tvquynh/scivetra-kb**.
2. Bấm nút xanh **Code** → **Download ZIP**.
3. Làm theo [mục 2.1](02-powershell-va-bao-mat.md#21-windows-chặn-file-tải-từ-internet): chuột phải file ZIP → **Properties** → ☑ **Unblock** → **OK**.

## 3.2 Giải nén đúng chỗ

Thư mục mã nguồn phải nằm ở **`C:\SciVetra\scivetra-kb`**, đường dẫn không dấu và không khoảng trắng.

1. Mở **File Explorer**, vào ổ **C:**, tạo thư mục mới tên **`SciVetra`**.
2. Chuột phải file ZIP → **Extract All…** (Giải nén tất cả) → chọn thư mục `C:\SciVetra` → **Extract**.
3. Bạn sẽ có thư mục `C:\SciVetra\scivetra-kb-main`. **Đổi tên** nó thành `scivetra-kb`.
4. Kiểm tra: mở `C:\SciVetra\scivetra-kb`; bên trong phải thấy các file `1-INSTALL.cmd`, `2-TRY-DEMO.cmd`, `3-OPEN-WORK-WINDOW.cmd` và thư mục `huong-dan`.

Nếu muốn làm nhanh bằng lệnh: mở PowerShell rồi dán cả khối dưới. Khối này giả định file ZIP tên `scivetra-kb-main.zip` nằm trong *Downloads*.

```powershell
New-Item -ItemType Directory -Force C:\SciVetra | Out-Null
Unblock-File "$HOME\Downloads\scivetra-kb-main.zip"
Expand-Archive "$HOME\Downloads\scivetra-kb-main.zip" -DestinationPath C:\SciVetra -Force
Rename-Item C:\SciVetra\scivetra-kb-main scivetra-kb
dir C:\SciVetra\scivetra-kb
```

## 3.3 Cài đặt (tự động)

Vào `C:\SciVetra\scivetra-kb` và **bấm đúp `1-INSTALL.cmd`**. Một cửa sổ hiện ra và chạy 6 bước:

| Bước | Việc |
|---|---|
| 1/6 | Tìm Python |
| 2/6 | Tạo các thư mục `data`, `backups`, `output` trong `C:\SciVetra` |
| 3/6 | Tạo **môi trường ảo** `C:\SciVetra\env`: một bản Python riêng cho SciVetra, không ảnh hưởng gì khác trên máy |
| 4/6 | Nối lệnh `rkb` (lệnh chính của SciVetra) |
| 5/6 | Tải **bộ đọc PDF** `pypdf 6.12.1` từ kho chính thức (cần Internet), rồi tự kiểm tra mã SHA-256 để chắc đúng bản gốc |
| 6/6 | Kiểm tra lại toàn bộ |

Thành công khi dòng cuối là **`=== INSTALLATION COMPLETE ===`**. Bấm phím bất kỳ để đóng.

- Không có Internet: bước 5 báo **NOTE**. SciVetra vẫn dùng được, chỉ thiếu phần đọc PDF. Khi có mạng, bấm đúp `1-INSTALL.cmd` lần nữa. Chạy lại bao nhiêu lần cũng an toàn.
- Thấy **ERROR**: xem [Xử lý lỗi](08-xu-ly-loi.md).

## 3.4 Cài đặt bằng tay (nếu không muốn dùng file bấm đúp)

Mở PowerShell rồi dán **từng khối**, đợi xong mới dán khối tiếp theo.

**Khối 1 — tạo môi trường ảo:**

```powershell
py -m venv C:\SciVetra\env
```

**Khối 2 — nối lệnh `rkb` vào mã nguồn:**

```powershell
$site = & C:\SciVetra\env\Scripts\python.exe -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
Set-Content -Path "$site\scivetra-kb-src.pth" -Value "C:\SciVetra\scivetra-kb\src" -Encoding ascii
Set-Content -Path C:\SciVetra\env\Scripts\rkb.cmd -Value '@"%~dp0python.exe" -m scivetra_kb %*' -Encoding ascii
C:\SciVetra\env\Scripts\rkb.cmd --version
```

Kết quả phải là `1.0.2` (hoặc bản mới hơn).

**Khối 3 — bộ đọc PDF (cần Internet):**

```powershell
New-Item -ItemType Directory -Force C:\SciVetra\packages | Out-Null
C:\SciVetra\env\Scripts\python.exe -m pip download "pypdf==6.12.1" --no-deps --only-binary=:all: -d C:\SciVetra\packages
(Get-FileHash C:\SciVetra\packages\pypdf-6.12.1-py3-none-any.whl).Hash
```

Dòng cuối phải là `8FA2A2321CF16247ED848BD7C97F193A60C08670D04ABED5B0138327E51C43B0`. **Nếu khác, đừng cài**: xóa file đó và tải lại. Nếu đúng, cài:

```powershell
C:\SciVetra\env\Scripts\python.exe -m pip install --no-index --no-deps C:\SciVetra\packages\pypdf-6.12.1-py3-none-any.whl
C:\SciVetra\env\Scripts\rkb.cmd parse runtime-check
```

Kết quả phải có chữ `CONTENT_MATCHES_OFFICIAL_WHEEL`.

## 3.5 Gỡ cài đặt

Xóa thư mục `C:\SciVetra\env` (và `C:\SciVetra\scivetra-kb` nếu muốn). Máy không còn gì khác của SciVetra.

**Cẩn thận:** `data` và `backups` chứa dữ liệu của bạn; chỉ xóa khi chắc chắn.
