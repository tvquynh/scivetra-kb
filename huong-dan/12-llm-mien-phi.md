# Dùng LLM miễn phí — mô hình trên máy và danh sách nhà cung cấp (giai đoạn L1–L2)

SciVetra đang được bổ sung LLM có khả năng **xoay vòng giữa nhiều dịch vụ miễn phí**: dịch vụ này hết hạn mức thì tự chuyển sang dịch vụ khác.

**Trạng thái:** SciVetra đã kiểm tra được danh sách nhà cung cấp, mô phỏng việc xoay vòng, và (**giai đoạn L2**) dùng được **mô hình chạy trên máy bạn** (Ollama hoặc LM Studio) để đề xuất bằng chứng. Dịch vụ trực tuyến thì chưa được gọi; đó là giai đoạn L3, sẽ làm khi bạn duyệt (thiết kế: `docs/ADR_LLM.md`).

## 1. Ba điều cần biết

| | |
|---|---|
| **Gói miễn phí thường dùng dữ liệu bạn gửi để huấn luyện mô hình** | Chỉ gửi dữ liệu tổng hợp, metadata, hoặc bài có giấy phép mở (ví dụ CC-BY). Toàn văn có bản quyền nên dùng mô hình **chạy trên máy bạn** (Ollama, LM Studio). SciVetra tự chặn dựa trên quyền sử dụng của từng bài. |
| **Nhiều nhà cung cấp thì được; mỗi nơi chỉ một tài khoản** | Tạo nhiều tài khoản ở cùng một nơi thường vi phạm điều khoản. |
| **Không bao giờ ghi key vào file** | Key nằm trong biến môi trường; file cấu hình chỉ ghi *tên* biến. |

## 2. Lập danh sách nhà cung cấp

1. **Sao chép file mẫu** (dán vào PowerShell):

   ```powershell
   copy C:\SciVetra\scivetra-kb\config\llm-providers.example.toml C:\SciVetra\llm-providers.toml
   ```

2. **Mở file:**

   ```powershell
   notepad C:\SciVetra\llm-providers.toml
   ```

   Với mỗi dịch vụ, kiểm tra:
   - `model`: tên mô hình tài khoản của bạn dùng được;
   - `data_policy`: theo điều khoản của dịch vụ. Chỉ ghi `NO_TRAINING` khi điều khoản nói rõ là không huấn luyện trên dữ liệu bạn gửi; nếu không thì ghi `MAY_TRAIN`; chưa chắc thì để `UNKNOWN`.
   - `priority`: số 1 được thử trước;
   - `limits`: hạn mức của gói miễn phí (`rpm` là số lần mỗi phút, `rpd` là số lần mỗi ngày).
3. **Xóa, hoặc đặt `enabled = false`,** cho các dịch vụ bạn chưa đăng ký.

## 3. Lưu key vào biến môi trường

Bạn tự đăng ký trên trang của từng dịch vụ và tạo API key ở đó. Sau đó, với mỗi key, dán lệnh sau vào PowerShell (thay `<dán-key-vào-đây>` bằng key của bạn):

```powershell
[Environment]::SetEnvironmentVariable("GROQ_API_KEY", "<dán-key-vào-đây>", "User")
```

Rồi mở **cửa sổ mới**.

**Không** dán key vào file cấu hình, vào kho dữ liệu, vào khung chat hay ảnh chụp màn hình.

## 4. Kiểm tra danh sách

Trong cửa sổ làm việc:

```powershell
rkb llm check-config --config C:\SciVetra\llm-providers.toml
```

Với mỗi dịch vụ:
- `key_present: true/false` cho biết biến đã được đặt hay chưa. Key không bao giờ hiện ra.
- `data_classes` cho biết dịch vụ đó được nhận loại dữ liệu nào.

**Xem dịch vụ nào sẽ được dùng cho một loại dữ liệu:**

```powershell
rkb llm plan --config C:\SciVetra\llm-providers.toml --data-class EXTERNAL_NO_TRAINING
```

| Loại dữ liệu | Nghĩa |
|---|---|
| `LOCAL_ONLY` | Chỉ mô hình chạy trên máy bạn |
| `EXTERNAL_NO_TRAINING` | Dịch vụ trực tuyến không huấn luyện trên dữ liệu bạn gửi |
| `EXTERNAL_ANY` | Mọi dịch vụ; chỉ dành cho dữ liệu tổng hợp, metadata hoặc giấy phép mở |

## 5. Xem mô phỏng xoay vòng (không cần Internet)

```powershell
rkb llm simulate --config C:\SciVetra\llm-providers.toml --scenario rotation
rkb llm simulate --config C:\SciVetra\llm-providers.toml --scenario breaker
rkb llm simulate --config C:\SciVetra\llm-providers.toml --scenario consensus
```

| Kịch bản | Cho thấy |
|---|---|
| `rotation` | Dịch vụ 1 "hết hạn mức", dịch vụ 2 "bị lỗi", nên dịch vụ 3 trả lời. Ở lần hỏi sau, dịch vụ 1 vẫn đang được cho nghỉ. |
| `breaker` | Sau 3 lần lỗi liên tiếp, dịch vụ bị tạm ngưng 5 phút |
| `consensus` | Hỏi hai dịch vụ khác nhau cùng một câu. Trùng nhau thì **AGREED**; khác nhau thì **DISAGREED** và giữ cả hai câu trả lời để con người quyết định. |

## 6. Chạy mô hình trên máy bạn (giai đoạn L2)

Mô hình chạy trên máy có ba lợi ích: miễn phí, không cần Internet, và bài báo không rời khỏi máy. Vì vậy đây là lựa chọn đúng cho toàn văn có bản quyền.
- **RAM:** cần khoảng **16 GB** cho mô hình 7–14B.
- **Card đồ họa:** có thì nhanh hơn nhiều; không có vẫn chạy được, nhưng chậm.

**1. Cài Ollama:** vào **https://ollama.com/download**, tải bộ cài cho Windows và chạy nó.

**2. Tải một mô hình** (vài GB, chỉ tải một lần). Dán vào PowerShell:

```powershell
ollama pull qwen3:8b
```

Mô hình lớn hơn (ví dụ `qwen3:14b`) cho kết quả tốt hơn nhưng chậm hơn. Sau khi cài, Ollama tự chạy nền.

**3. Khai báo trong file cấu hình:** trong `llm-providers.toml`, mục `local-ollama` phải ghi đúng tên mô hình bạn đã tải, ví dụ `model = "qwen3:8b"`.

Nếu dùng LM Studio thay cho Ollama: bật local server của LM Studio, rồi ghi:
- `base_url = "http://127.0.0.1:1234/v1"`;
- tên mô hình của LM Studio;
- `json_mode = false`.

**4. Kiểm tra kết nối:**

```powershell
rkb llm ping --config C:\SciVetra\llm-providers.toml
```

Thấy `"status": "OK"` là SciVetra đã nói chuyện được với mô hình trên máy. Nếu không, kết quả có kèm gợi ý cách sửa.

**5. Cho mô hình đề xuất bằng chứng** cho một phạm vi bằng chứng S08:

```powershell
rkb llm extract-evidence --data-root C:\SciVetra\data\<TEN-KHO> --scope-id <MA-PHAM-VI-BANG-CHUNG> --config C:\SciVetra\llm-providers.toml --operator <TEN-BAN>
```

- **Câu trả lời của mô hình chỉ là đề xuất.** SciVetra đối chiếu từng câu trích với PDF và từng con số với câu trích. Sau đó người duyệt kiểm tra và chấp nhận như trước (`rkb evidence verify`, `rkb evidence review`). Câu trích hay số liệu bịa ra sẽ không qua được bước kiểm tra.
- **Muốn chắc hơn:** nếu có hai mô hình khác nhau trên máy, thêm `--consensus`. Khi đó chỉ bản ghi được cả hai mô hình trả lời giống nhau mới thành đề xuất.
- **Quyền sử dụng:** chỉ những bài được phép xử lý cục bộ mới được gửi, và chỉ gửi tới mô hình trên máy bạn.

## 7. Claude Desktop và Claude Code

- **Claude Desktop** đã kết nối được với kho của bạn ở chế độ chỉ đọc, qua máy chủ MCP của SciVetra, với hồ sơ quyền do bạn duyệt (xem `docs/S10_CLAUDE_DESKTOP_GUIDE.md`, tiếng Anh).
- **Claude Code** dùng được cùng máy chủ đó, hoặc chạy các lệnh `rkb` cho bạn.

Gói Claude trả theo tháng dành cho dùng tương tác, nên SciVetra sẽ không dùng nó làm "máy chạy tự động phía sau"; việc gọi tự động sẽ dùng API key.
