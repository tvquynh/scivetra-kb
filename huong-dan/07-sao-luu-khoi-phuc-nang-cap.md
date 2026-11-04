# Sao lưu, khôi phục, nâng cấp

## 7.1 Sao lưu — làm hằng tuần và trước mọi việc lớn

```powershell
rkb backup --data-root C:\SciVetra\data\<TEN-KHO> --target C:\SciVetra\backups
```

Lệnh tạo một thư mục mới `C:\SciVetra\backups\rkb-backup-<NGAY-GIO>`. **Kiểm tra lại ngay** (thay bằng tên thư mục vừa tạo):

```powershell
rkb verify-backup --backup C:\SciVetra\backups\<TEN-BAN-SAO-LUU>
```

Thấy `"valid":true` là bản sao lưu dùng được.

**Nên chép thêm thư mục sao lưu ra ổ cứng ngoài hoặc ổ mạng.** Nếu máy hỏng, bản sao lưu nằm cùng máy cũng mất theo.

## 7.2 Khôi phục

Khôi phục luôn vào một **thư mục mới**; kho cũ được giữ nguyên.

```powershell
rkb restore --backup C:\SciVetra\backups\<TEN-BAN-SAO-LUU> --data-root C:\SciVetra\data\<TEN-KHO>-khoi-phuc
rkb doctor --data-root C:\SciVetra\data\<TEN-KHO>-khoi-phuc --full
```

Kiểm tra kho khôi phục thấy ổn thì làm việc tiếp trên kho đó.

## 7.3 Cập nhật SciVetra lên bản mới

1. **Sao lưu** mọi kho (mục 7.1).
2. Tải bản mới (bước 3). Đổi tên thư mục cũ `C:\SciVetra\scivetra-kb` thành `scivetra-kb-cu`, rồi giải nén bản mới thành `C:\SciVetra\scivetra-kb`.
3. Bấm đúp `1-INSTALL.cmd` của bản mới.
4. Nếu lệnh `rkb` báo **`KB_UPGRADE_REQUIRED`**, kho cần nâng cấp:

   ```powershell
   rkb upgrade --data-root C:\SciVetra\data\<TEN-KHO> --backup-target C:\SciVetra\backups
   rkb doctor --data-root C:\SciVetra\data\<TEN-KHO> --full
   ```

   Lệnh nâng cấp **tự sao lưu trước**, nâng cấp trên một bản sao, kiểm tra kỹ, rồi mới thay thế.

## 7.4 Quay lại bản cũ (nếu nâng cấp có vấn đề)

- Trong thư mục `C:\SciVetra\data\<TEN-KHO>\db` có file `kb.sqlite.pre-<SO>-<NGAY-GIO>`: đó là cơ sở dữ liệu nguyên bản trước khi nâng cấp.
- **Cách an toàn nhất:** khôi phục bản sao lưu mà lệnh nâng cấp đã tạo vào một thư mục mới (mục 7.2), rồi dùng bản SciVetra cũ (`scivetra-kb-cu`) với thư mục đó.
