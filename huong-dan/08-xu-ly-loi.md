# Xử lý lỗi thường gặp

## 1. `py` hoặc `python` không được nhận ra

**Thông báo:** *"The term 'py' is not recognized"*, hoặc gõ `python` thì Microsoft Store mở ra.

1. Cài lại Python theo [bước 1](01-cai-dat-python.md), nhớ đánh dấu **Add python.exe to PATH**.
2. Đóng mọi cửa sổ PowerShell và mở lại. Nếu vẫn lỗi, khởi động lại máy.
3. Tắt "bí danh" Python của Store: **Settings** → **Apps** → **Advanced app settings** → **App execution aliases** → tắt **python.exe** và **python3.exe**.

## 2. "running scripts is disabled on this system"

Bạn đang tự chạy file `.ps1`. Có hai cách:
- dùng các file bấm đúp `.cmd`; hoặc
- làm [mục 2.2](02-powershell-va-bao-mat.md#22-cho-phép-chạy-script-powershell-chỉ-khi-cần).

## 3. "Windows protected your PC" khi bấm đúp `.cmd`

Bấm **More info** → **Run anyway**, hoặc bỏ chặn file ZIP trước khi giải nén ([mục 2.1](02-powershell-va-bao-mat.md#21-windows-chặn-file-tải-từ-internet)).

## 4. Cài đặt báo "The path contains accented or special characters"

Bạn đã giải nén vào thư mục có dấu hoặc có khoảng trắng, ví dụ trong *Tài liệu*. Hãy chuyển mã nguồn về đúng `C:\SciVetra\scivetra-kb` ([bước 3.2](03-tai-va-cai-dat-scivetra.md#32-giải-nén-đúng-chỗ)).

## 5. Cài đặt báo "Could not download pypdf"

Máy không có Internet, hoặc mạng chặn PyPI. Mọi thứ khác vẫn dùng được. Khi có mạng, bấm đúp `1-INSTALL.cmd` lại.

Nếu báo **"wrong SHA-256"**: file tải về không phải bản gốc nên đã bị xóa. Hãy thử lại, hoặc dùng mạng khác.

## 6. `rkb` không được nhận ra

Bạn đang ở một cửa sổ PowerShell thường. Hãy mở **`3-OPEN-WORK-WINDOW.cmd`**, hoặc gõ đầy đủ đường dẫn:

```powershell
C:\SciVetra\env\Scripts\rkb.cmd --version
```

## 7. Các mã lỗi của `rkb`

| Mã lỗi | Nghĩa | Làm gì |
|---|---|---|
| `DATA_ROOT_REQUIRED` | Thiếu `--data-root` | Thêm `--data-root C:\SciVetra\data\<TEN-KHO>` |
| `KB_NOT_INITIALIZED` | Thư mục chưa phải là kho | Kiểm tra lại tên kho, hoặc chạy `rkb init` |
| `WRITER_ACTIVE` | Một lệnh `rkb` khác đang ghi vào kho | Chờ lệnh kia xong; đóng các cửa sổ `rkb` khác |
| `PROTECTED_DATA_ROOT` | Thư mục `E:\ResearchKB` được bảo vệ (chờ chủ dự án duyệt) | Dùng thư mục trong `C:\SciVetra\data` |
| `KB_UPGRADE_REQUIRED` | Kho tạo bằng bản cũ | Xem [mục 7.3](07-sao-luu-khoi-phuc-nang-cap.md) |
| `KB_CLIENT_TOO_OLD` | Kho tạo bằng bản mới hơn SciVetra đang cài | Cập nhật SciVetra |
| `LIVE_ACCESS_NOT_AUTHORIZED` | Truy cập mạng chưa được cho phép trong bản này | Bình thường; xem [giới hạn](10-gioi-han-va-an-toan.md) |
| `PARSER_NOT_INSTALLED` | Chưa có bộ đọc PDF | Chạy lại `1-INSTALL.cmd` khi có Internet |
| `OUT_DIR_INVALID`, `EXPORT_FOLDER_EXISTS` | Thư mục xuất không hợp lệ hoặc đã tồn tại | Dùng thư mục mới, đường dẫn đầy đủ, nằm ngoài thư mục kho |
| `HUMAN_TEXT_SOURCE_EXCERPT_REFUSED` | Bạn dán nguyên văn đoạn trong PDF vào ghi chú, trong khi quyền không cho lưu trích dẫn | Viết lại bằng lời của bạn |
| `SELF_REVIEW_REFUSED` | Người viết không được tự duyệt | Nhờ người khác duyệt |
| `EXPORT_RELEASE_REFUSED` | Quyền, nguồn hoặc bằng chứng vừa thay đổi lúc đang xuất | Chạy lại `rkb synth audit`, rồi xuất lại |
| `BACKUP_SOURCE_INCOMPLETE` | Kho thiếu file, sao lưu bị từ chối | Chạy `rkb doctor --full`, rồi khôi phục từ bản sao lưu gần nhất |

## 8. Vẫn không được?

Chạy hai lệnh dưới, chụp màn hình kết quả và gửi cho người hỗ trợ:

```powershell
rkb --version
rkb doctor --data-root C:\SciVetra\data\<TEN-KHO>
```

Kết quả `doctor` không chứa nội dung bài báo, chỉ có số đếm và tình trạng.
