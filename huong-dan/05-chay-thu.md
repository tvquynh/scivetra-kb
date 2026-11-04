# Chạy thử với dữ liệu mẫu

Bạn có thể xem SciVetra hoạt động mà **không cần dữ liệu thật**. Bấm đúp **`C:\SciVetra\scivetra-kb\2-TRY-DEMO.cmd`**.

## Phần A (khoảng 1 phút): kho dữ liệu thử nghiệm

| Dòng thấy được | Ý nghĩa |
|---|---|
| `Created knowledge base: C:\SciVetra\data\demo-...` | Đã tạo một kho mới |
| `Health check: no problems` | Kiểm tra sức khỏe: cơ sở dữ liệu và kho file đều nguyên vẹn |
| `Backed up to: C:\SciVetra\backups\rkb-backup-...` | Đã sao lưu toàn bộ kho |
| `Backup verified: valid` | Bản sao lưu đã được kiểm tra lại từng file, khôi phục được |

## Phần B (5–10 phút, cần bộ đọc PDF): bản nháp tổng hợp khoa học

Máy tự làm toàn bộ quy trình trên **32 bài báo giả lập** (PDF do máy tạo ra, nội dung giả định về phát hiện mã độc):

1. đọc từng PDF trong hộp cách ly;
2. trích các bằng chứng như số liệu TPR và ROC-AUC, giới hạn, khoảng tin cậy;
3. "người duyệt giả lập" chấp nhận bằng chứng;
4. đặt ra **284 nhận định** để kiểm tra, trong đó cố ý có nhiều nhận định **sai**;
5. kiểm tra từng nhận định và xuất **bản nháp**.

Khi xong, cửa sổ in ra (thông báo bằng tiếng Anh):

```
OK    284 claims from 32 simulated papers
OK    Agreement with the answer key: 100%; wrongly supported claims: 0; citation traceability: 100%
```

| Câu | Nghĩa |
|---|---|
| *Agreement 100%*: khớp đáp án 100% | Phán định của máy trùng với đáp án đã biết trước |
| *Wrongly supported claims: 0*: không có nhận định sai nào được coi là đúng | Không nhận định sai nào bị coi là đúng |
| *Citation traceability 100%*: truy vết trích dẫn 100% | Mọi nhận định hoặc có nguồn, hoặc bị đánh dấu rõ |

**Notepad tự mở file `draft.md`**, là bản nháp. Mỗi dòng là một nhận định:
- **`[1]`, `[2]`…** là được bằng chứng ủng hộ, kèm số tài liệu tham khảo;
- **`[UNVERIFIED]`** là chưa kiểm chứng, kèm lý do, ví dụ `MISMATCH:value` (số liệu không khớp);
- **`[CONFLICTED]`** là bài báo tự mâu thuẫn.

Các file kết quả nằm trong `C:\SciVetra\output\demo-draft-...`:

| File | Nội dung |
|---|---|
| `s11_evaluation_cases.csv` | Bảng từng nhận định; mở được bằng Excel |
| `s11_evaluation_summary.json` | Các con số tổng hợp |
| `run1\audited_example_draft\...\draft.md` | Bản nháp |
| `evidence_matrix.csv` (cùng thư mục với bản nháp) | Bảng nhận định nối với bằng chứng (trang, vị trí) |

> Đây là **dữ liệu giả lập** để minh họa. Kết quả **không** đo độ chính xác trên bài báo thật.

## Chỉ chạy phần A

Mở cửa sổ làm việc rồi dán:

```powershell
C:\SciVetra\scivetra-kb\2-TRY-DEMO.cmd -PartAOnly
```
