# SciVetra KB

**Kho tri thức nghiên cứu chạy ngay trên máy tính của bạn**, dành cho người làm tổng quan tài liệu khoa học. Dữ liệu không rời khỏi máy, quyền sử dụng tài liệu được tôn trọng, và **con người luôn là người quyết định**.

> 🇬🇧 **English user guide (Windows, macOS, Linux): [guide/00-START-HERE.md](guide/00-START-HERE.md)** — English summary at the bottom of this page.

## SciVetra giúp bạn làm gì?

- **Quản lý thư viện bài báo:** DOI, phiên bản, bài bị rút hay đính chính; không bao giờ tự gộp nhầm hai bài.
- **Nhận file PDF** tải bằng PaperBatchDownloader, kiểm tra từng file trước khi lưu.
- **Ghi quyền sử dụng** của từng tài liệu (được lưu, được trích, được chia sẻ…). Mặc định là *không*.
- **Đọc PDF trong hộp cách ly**, lưu nội dung kèm số trang và vị trí.
- **Ghi lại bằng chứng khoa học** (số liệu, kết quả, hạn chế) để người duyệt kiểm tra và chấp nhận.
- **Tìm kiếm** trong các bài bạn có quyền.
- **Lập bản nháp tổng hợp:** mỗi nhận định đều có trích dẫn, hoặc được đánh dấu rõ `[UNVERIFIED]` (chưa kiểm chứng).

## Bắt đầu trong 4 bước (không cần biết lập trình)

| | Việc | Hướng dẫn |
|---|---|---|
| 1 | Cài Python | [huong-dan/01-cai-dat-python.md](huong-dan/01-cai-dat-python.md) |
| 2 | Tải SciVetra, giải nén vào `C:\SciVetra\scivetra-kb` | [huong-dan/03-tai-va-cai-dat-scivetra.md](huong-dan/03-tai-va-cai-dat-scivetra.md) |
| 3 | Bấm đúp **`1-INSTALL.cmd`** (cài đặt) | tự động, khoảng 2 phút |
| 4 | Bấm đúp **`2-TRY-DEMO.cmd`** (chạy thử) để xem SciVetra chạy trên dữ liệu mẫu | [huong-dan/05-chay-thu.md](huong-dan/05-chay-thu.md) |

Sau đó, mỗi lần làm việc chỉ cần bấm đúp **`3-OPEN-WORK-WINDOW.cmd`** (mở cửa sổ làm việc).

Dùng macOS hoặc Linux? Xem [hướng dẫn tiếng Anh](guide/00-START-HERE.md): chạy `bash 1-install.sh`, `bash 2-try-demo.sh`, `bash 3-work-shell.sh`.

👉 **Toàn bộ hướng dẫn, từng bước có lệnh để sao chép:** [huong-dan/00-BAT-DAU-TU-DAY.md](huong-dan/00-BAT-DAU-TU-DAY.md)

## Mục lục hướng dẫn

1. [Cài Python](huong-dan/01-cai-dat-python.md)
2. [PowerShell và cài đặt bảo mật](huong-dan/02-powershell-va-bao-mat.md)
3. [Tải và cài đặt SciVetra](huong-dan/03-tai-va-cai-dat-scivetra.md)
4. [Lần đầu sử dụng](huong-dan/04-lan-dau-su-dung.md)
5. [Chạy thử với dữ liệu mẫu](huong-dan/05-chay-thu.md)
6. [Sử dụng hằng ngày](huong-dan/06-su-dung-hang-ngay.md)
7. [Sao lưu, khôi phục, nâng cấp](huong-dan/07-sao-luu-khoi-phuc-nang-cap.md)
8. [Xử lý lỗi](huong-dan/08-xu-ly-loi.md)
9. [Từ điển thuật ngữ](huong-dan/09-tu-dien-thuat-ngu.md)
10. [Giới hạn và an toàn](huong-dan/10-gioi-han-va-an-toan.md)
11. [Dùng LLM miễn phí và mô hình trên máy (giai đoạn L1–L2)](huong-dan/12-llm-mien-phi.md)

## Trạng thái

- **Phiên bản 1.0.0**, phát hành trên GitHub (không có trên PyPI).
- **Đã kiểm tra:** 654 bài kiểm tra tự động trên Python 3.11 và 3.14, cùng 8 cổng kiểm tra phát hành.
- **AI:** dùng được mô hình chạy trên máy bạn (Ollama / LM Studio) để *đề xuất* bằng chứng; chưa gọi dịch vụ AI trực tuyến.
- **Dữ liệu và PDF thật** cần chủ dự án cho phép. Xem [Giới hạn và an toàn](huong-dan/10-gioi-han-va-an-toan.md).
- **Giấy phép:** [Apache 2.0](LICENSE).

## Cấu trúc thư mục

| Thư mục / file | Dành cho |
|---|---|
| `1-INSTALL.cmd`, `2-TRY-DEMO.cmd`, `3-OPEN-WORK-WINDOW.cmd` | Người dùng Windows: bấm đúp |
| `1-install.sh`, `2-try-demo.sh`, `3-work-shell.sh` | Người dùng macOS / Linux: `bash <tên-file>` |
| `guide/` | Người dùng: hướng dẫn tiếng Anh (Windows, macOS, Linux) |
| `huong-dan/` | Người dùng: hướng dẫn tiếng Việt |
| `scripts/` | Các script mà file bấm đúp gọi tới |
| `docs/` | Tài liệu kỹ thuật (tiếng Anh): kiến trúc, quyết định thiết kế, hướng dẫn chi tiết từng giai đoạn |
| `src/` | Mã nguồn |
| `tests/`, `tools/` | Bộ kiểm tra tự động và công cụ phát hành |

---

## English summary

**SciVetra KB** is an offline, local research knowledge base for systematic reviews. It covers:
- metadata and publication-integrity tracking;
- a research library;
- a PaperBatchDownloader bridge;
- rights-gated, sandboxed PDF parsing;
- AI-assisted *proposals* (a deterministic mock; no real model);
- human-accepted evidence records;
- rights-checked search;
- synthesis drafts in which every claim is cited or visibly flagged.

Humans make every decision. The runtime uses the Python standard library only, plus the optional pinned `pypdf` parser.

**📘 User guide (non-specialists, step by step, Windows / macOS / Linux): [guide/00-START-HERE.md](guide/00-START-HERE.md)**

| | 🪟 Windows | 🍎 macOS / 🐧 Linux |
|---|---|---|
| Put the code in | `C:\SciVetra\scivetra-kb` | `~/SciVetra/scivetra-kb` |
| Install | double-click `1-INSTALL.cmd` | `bash 1-install.sh` |
| Demo on sample data | double-click `2-TRY-DEMO.cmd` | `bash 2-try-demo.sh` |
| Work window | double-click `3-OPEN-WORK-WINDOW.cmd` | `bash 3-work-shell.sh` |

All need Python 3.11 or newer ([guide/01-install-python.md](guide/01-install-python.md)).

| If you want to | Read |
|---|---|
| Understand what it does and use it well | [guide/11-how-scivetra-works.md](guide/11-how-scivetra-works.md) |
| See every command | `docs/COMMAND_REFERENCE.md` |
| Develop or review | `docs/TECHNICAL_OVERVIEW.md`, `docs/ADR_S*.md`, `docs/SECURITY_REVIEW_S12.md` |
| Run the tests | `python -I tools/run_offline_tests.py` |
| Run the release gates | `python -I tools/s12_release_readiness.py --work DIR --out FILE.json` |

Licensed under the [Apache License 2.0](LICENSE). Version `1.0.0`, released on GitHub; not on PyPI.
