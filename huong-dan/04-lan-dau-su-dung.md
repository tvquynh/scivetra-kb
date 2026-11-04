# Lần đầu sử dụng — cửa sổ làm việc và cách đọc kết quả

## 4.1 Mở cửa sổ làm việc

Bấm đúp **`C:\SciVetra\scivetra-kb\3-OPEN-WORK-WINDOW.cmd`**. Một cửa sổ PowerShell mở ra:
- dấu nhắc bắt đầu bằng `(SciVetra)`;
- thư mục hiện tại là `C:\SciVetra`;
- lệnh **`rkb`** dùng được ngay.

Mỗi lần làm việc, hãy mở cửa sổ này; các lệnh trong hướng dẫn đều gõ ở đây.

> **Muốn tự mở bằng PowerShell thường?** Dán lệnh dưới. Lệnh này cần đã làm [mục 2.2](02-powershell-va-bao-mat.md#22-cho-phép-chạy-script-powershell-chỉ-khi-cần).
>
> ```powershell
> C:\SciVetra\env\Scripts\Activate.ps1
> ```

## 4.2 Tạo kho dữ liệu (KB) đầu tiên

Một **kho** là một thư mục chứa toàn bộ dữ liệu của một dự án. Mỗi dự án nên có một kho riêng. Tên kho viết không dấu và không khoảng trắng, ví dụ `du-an-1`.

```powershell
rkb init --data-root C:\SciVetra\data\du-an-1
```

Kết quả có `"initialized":true`.

## 4.3 Đọc kết quả

Mọi lệnh `rkb` trả về **một dòng dữ liệu dạng JSON**, gồm các cặp `"tên":giá trị`. Bạn chỉ cần nhìn mấy chỗ:

| Thấy | Nghĩa là |
|---|---|
| `"ok":true` | Lệnh thành công |
| `"ok":false,"error":"MA_LOI"` | Lệnh bị từ chối, có kèm mã lỗi. Tra mã trong [Xử lý lỗi](08-xu-ly-loi.md). |
| `"problems":[]` | Không có vấn đề (ngoặc vuông rỗng) |

**Xem kết quả dễ đọc hơn.** Thêm `| ConvertFrom-Json` vào cuối lệnh để mỗi trường hiện trên một dòng:

```powershell
rkb doctor --data-root C:\SciVetra\data\du-an-1 | ConvertFrom-Json | Format-List ok, problems, warnings, integrity_check
```

## 4.4 Những từ sẽ gặp thường xuyên

| Từ | Nghĩa |
|---|---|
| `NON_AUTHORITATIVE` | Chỉ là đề xuất hay bản nháp, chưa phải quyết định chính thức |
| `[UNVERIFIED]` | Nhận định chưa kiểm chứng được bằng bằng chứng hiện có |
| `[CONFLICTED]` | Cùng một bài báo có hai số liệu mâu thuẫn |
| `WITHHELD` | Bị ẩn vì quyền sử dụng hiện tại không cho phép hiển thị |
| `STALE` | File gốc đã thay đổi hoặc hỏng, nên bằng chứng cũ không còn tin được |
| `BLOCKED_RIGHTS` | Quyền sử dụng bị rút |

Xem thêm trong [Từ điển thuật ngữ](09-tu-dien-thuat-ngu.md).

## 4.5 Ba thói quen tốt

1. **Kiểm tra sức khỏe** kho trước khi làm việc: `rkb doctor --data-root ...`.
2. **Sao lưu** trước mọi việc quan trọng ([bước 7](07-sao-luu-khoi-phuc-nang-cap.md)).
3. **Không sửa, xóa hay di chuyển** file bên trong thư mục kho bằng tay; luôn dùng lệnh `rkb`.
