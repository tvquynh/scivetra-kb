# Sử dụng hằng ngày — các lệnh để sao chép

Tất cả lệnh gõ trong **cửa sổ làm việc** (bấm đúp `3-OPEN-WORK-WINDOW.cmd`).

**Quy ước:** chữ VIẾT HOA nằm giữa `<` `>` là chỗ **bạn phải thay** bằng giá trị của mình, và **xóa luôn dấu `<` `>`**. Ví dụ `<TEN-KHO>` thay bằng `du-an-1`.

> **Dữ liệu thật cần chủ dự án cho phép.** Phiên bản này đã được kiểm tra với dữ liệu giả lập. Trước khi dùng PDF hoặc dự án thật, đọc [Giới hạn và an toàn](10-gioi-han-va-an-toan.md).

## A. Quản lý kho dữ liệu (dùng được ngay)

| Việc | Lệnh |
|---|---|
| Tạo kho mới | `rkb init --data-root C:\SciVetra\data\<TEN-KHO>` |
| Kiểm tra sức khỏe nhanh | `rkb doctor --data-root C:\SciVetra\data\<TEN-KHO>` |
| Kiểm tra kỹ (băm lại mọi file) | `rkb doctor --data-root C:\SciVetra\data\<TEN-KHO> --full` |
| Sao lưu | `rkb backup --data-root C:\SciVetra\data\<TEN-KHO> --target C:\SciVetra\backups` |
| Xem danh sách bản sao lưu | `dir C:\SciVetra\backups` |
| Kiểm tra một bản sao lưu | `rkb verify-backup --backup C:\SciVetra\backups\<TEN-BAN-SAO-LUU>` |
| Khôi phục vào kho **mới** | `rkb restore --backup C:\SciVetra\backups\<TEN-BAN-SAO-LUU> --data-root C:\SciVetra\data\<TEN-KHO-MOI>` |
| Xem phiên bản | `rkb --version` |

Chi tiết: [Sao lưu, khôi phục, nâng cấp](07-sao-luu-khoi-phuc-nang-cap.md).

## B. Thư viện bài báo (từ Reprovane)

**Cần có:** thư mục "Stage-11 corpus export" xuất từ Reprovane.

| Việc | Lệnh |
|---|---|
| Nhập danh sách bài từ Reprovane | `rkb library import-reprovane --data-root C:\SciVetra\data\<TEN-KHO> --export-dir <THU-MUC-EXPORT> --workspace-label <TEN-DU-AN> --operator <TEN-BAN>` |
| Tìm theo DOI | `rkb library search --data-root C:\SciVetra\data\<TEN-KHO> --doi <DOI>` |
| Tìm theo tên bài | `rkb library search --data-root C:\SciVetra\data\<TEN-KHO> --title "<MOT-PHAN-TEN-BAI>"` |
| Xem việc cần người duyệt | `rkb library review-queue --data-root C:\SciVetra\data\<TEN-KHO>` |

`<TEN-BAN>` là nhãn tên của bạn, ví dụ `quynh`, dùng để ghi ai đã làm gì.

## C. Tải PDF bằng PaperBatchDownloader (PBD)

**Cần có:** thư mục "Stage-11B full-text export" của Reprovane, và phần mềm PBD.

1. Tạo danh sách để đưa cho PBD (thư mục `--out-dir` phải là thư mục **mới**):

   ```powershell
   rkb bridge export --data-root C:\SciVetra\data\<TEN-KHO> --source-dir <THU-MUC-STAGE-11B> --kind rv-manual-queue --workspace-label <TEN-DU-AN> --out-dir C:\SciVetra\output\pbd-dot-1 --operator <TEN-BAN>
   ```

2. Mở PBD, nạp file `pbd_input_....csv` trong `C:\SciVetra\output\pbd-dot-1`, và cho PBD lưu PDF vào thư mục `pbd_output_...` bên cạnh.
3. Nhập kết quả. `<MA-DOT>` là giá trị `batch_id` mà bước 1 in ra (bắt đầu bằng `ab_`).

   ```powershell
   rkb bridge import --data-root C:\SciVetra\data\<TEN-KHO> --batch-id <MA-DOT> --result-csv <FILE-CSV-KET-QUA-CUA-PBD> --pdf-folder <THU-MUC-PDF-CUA-PBD> --operator <TEN-BAN>
   rkb bridge status --data-root C:\SciVetra\data\<TEN-KHO> --batch-id <MA-DOT>
   ```

Mỗi PDF đều được kiểm tra cấu trúc và đối chiếu trang tên bài trước khi vào kho. Chi tiết (tiếng Anh): `docs/BRIDGE_USER_GUIDE.md`.

## D. Quyền sử dụng và đọc PDF

| Việc | Lệnh |
|---|---|
| Kiểm tra bộ đọc PDF | `rkb parse runtime-check` |
| Đọc một PDF (cần quyền "xử lý cục bộ") | `rkb parse run --data-root C:\SciVetra\data\<TEN-KHO> --asset <MA-FILE> --link-id <MA-LIEN-KET> --operator <TEN-BAN> --purpose RESEARCH_PARSE` |
| Xem tình trạng đọc của một file | `rkb parse status --data-root C:\SciVetra\data\<TEN-KHO> --asset <MA-FILE>` |
| Hỏi một quyền có được phép không | `rkb rights gate --data-root C:\SciVetra\data\<TEN-KHO> --asset <MA-FILE> --document-version-id <MA-PHIEN-BAN> --action A2` |

`<MA-FILE>`, `<MA-LIEN-KET>` và `<MA-PHIEN-BAN>` lấy từ kết quả các lệnh trước (`asset_sha256`, `link_id`, `document_version_id`).

Ghi quyền sử dụng (`rkb rights assess`) cần một file mô tả giấy phép của bài; xem `docs/CLI_GUIDE.md`, mục *Assets and rights*. Mặc định mọi quyền đều là **TỪ CHỐI** cho đến khi có đánh giá.

**Trên Windows, chỉ đọc PDF do bạn tin tưởng** ([lý do](10-gioi-han-va-an-toan.md)). Chi tiết (tiếng Anh): `docs/PARSE_USER_GUIDE.md`.

## E. Xét chọn toàn văn, trích bằng chứng, tìm kiếm

Các bước này dành cho nhóm nghiên cứu có **ít nhất hai người duyệt**; máy không tự quyết.

| Giai đoạn | Lệnh chính | Hướng dẫn chi tiết (tiếng Anh) |
|---|---|---|
| Xét chọn toàn văn | `rkb fte protocol-create`, `rkb fte create`, `rkb fte decide`, `rkb fte status` | `docs/FTE_USER_GUIDE.md` |
| Đề xuất sàng lọc (máy giả lập, không AI thật) | `rkb screen scope-create`, `rkb screen run-mock`, `rkb screen initial-judgement` | `docs/SCREENING_USER_GUIDE.md` |
| Trích bằng chứng | `rkb evidence scope-create`, `rkb evidence run-mock`, `rkb evidence verify`, `rkb evidence review` | `docs/EVIDENCE_USER_GUIDE.md` |
| Tìm kiếm | `rkb index build`, `rkb search passages`, `rkb rag context` | `docs/RETRIEVAL_USER_GUIDE.md` |

Ví dụ tìm kiếm khi đã có phạm vi tìm kiếm (`<MA-PHAM-VI>`):

```powershell
rkb search passages --data-root C:\SciVetra\data\<TEN-KHO> --scope-id <MA-PHAM-VI> --query "<TU-KHOA>" --operator <TEN-BAN>
```

## F. Bản nháp tổng hợp và kiểm tra trích dẫn

**Cần có:** bằng chứng đã được người duyệt chấp nhận (mục E).

1. Tạo phạm vi tổng hợp từ phạm vi bằng chứng `<MA-PHAM-VI-BANG-CHUNG>`:

   ```powershell
   rkb synth scope-create --data-root C:\SciVetra\data\<TEN-KHO> --evidence-scope-id <MA-PHAM-VI-BANG-CHUNG> --label "<TEN-BAN-NHAP>" --operator <TEN-BAN> --review-policy SINGLE_HUMAN --review-policy-reason "<LY-DO>"
   ```

2. Viết một nhận định vào file. Mở Notepad bằng `notepad C:\SciVetra\output\nhan-dinh-1.json`, dán nội dung mẫu dưới, sửa số liệu cho đúng, rồi lưu:

   ```json
   {"schema": "scivetra-claim-spec/1", "kind": "REPORTED_VALUE",
    "measurement": {"metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%",
                    "dataset": "EMBER", "dataset_year": "2018", "split": "TEST"}}
   ```

3. Thêm nhận định, kiểm tra, rồi xuất bản nháp. `<MA-BANG-CHUNG>` bắt đầu bằng `evd_`.

   ```powershell
   rkb synth claim-add --data-root C:\SciVetra\data\<TEN-KHO> --scope-id <MA-PHAM-VI-TONG-HOP> --claim-key N1 --spec-file C:\SciVetra\output\nhan-dinh-1.json --cite <MA-BANG-CHUNG> --operator <TEN-BAN>
   rkb synth audit --data-root C:\SciVetra\data\<TEN-KHO> --scope-id <MA-PHAM-VI-TONG-HOP> --operator <TEN-BAN>
   rkb synth export --data-root C:\SciVetra\data\<TEN-KHO> --scope-id <MA-PHAM-VI-TONG-HOP> --out-dir C:\SciVetra\output\ban-nhap-1 --operator <TEN-BAN>
   ```

4. **Một người khác** (không phải người viết nhận định) duyệt bản nháp:

   ```powershell
   rkb synth review --data-root C:\SciVetra\data\<TEN-KHO> --scope-id <MA-PHAM-VI-TONG-HOP> --audit-id <MA-KIEM-TRA> --reviewer <TEN-NGUOI-DUYET> --decision ATTEST_DRAFT_ACCURATE --reason-code UNVERIFIED_CLAIMS_ACKNOWLEDGED
   ```

Chi tiết (tiếng Anh): `docs/S11_SYNTHESIS_GUIDE.md`.

## G. Danh sách đầy đủ 123 lệnh

Xem `docs/COMMAND_REFERENCE.md`. Muốn xem trợ giúp của một lệnh bất kỳ, thêm `--help`:

```powershell
rkb synth audit --help
```
