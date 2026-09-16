# Báo cáo phase STT streaming – hồ sơ go-live

Ngày kiểm tra: 16/09/2026

## Kết quả phase

Luồng STT streaming đã hoạt động trên môi trường local theo kiến trúc:

`PFM/M-Your → POST /api/stt/session → ticket ngắn hạn → WebSocket STT → partial/final transcript`

Backend STT đã bổ sung:

- API cấp ticket `POST /api/v1/stream-sessions`; ticket hết hạn, gắn với browser Origin và không đưa service key xuống trình duyệt.
- WebSocket `/api/v1/transcriptions/stream`, nhận PCM16 little-endian, mono, 16 kHz theo từng gói âm thanh.
- Sự kiện `ready`, `partial`, `finishing`, `final` và `error`.
- Kết thúc câu bằng nút dừng, khoảng lặng hoặc giới hạn thời lượng; có VAD, giới hạn phiên đồng thời và kiểm soát tốc độ gửi audio.
- Dùng chung PhoWhisper pipeline đã cache và khóa inference để tránh chạy chồng model.
- Script kiểm tra public gateway và bộ test cho ticket, WebSocket, buffer, VAD và lỗi giao thức.

PFM/M-Your đã bổ sung:

- Proxy tạo ticket phía server; `STT_SERVICE_API_KEY` không xuất hiện trong browser bundle.
- AudioWorklet thu âm, resample về PCM16 mono 16 kHz và gửi theo WebSocket.
- Partial transcript thay thế bản nháp hiện tại; final transcript được đưa vào ô chat và chỉ gửi khi người dùng bấm gửi.
- Dừng/huỷ microphone khi đóng M-Your, xử lý timeout, mất mạng và quyền microphone.
- Lỗi Agent Backend/lịch sử chat không còn khóa chức năng thử STT.
- `localhost:3000` và `127.0.0.1:3000` được coi là hai alias loopback hợp lệ; origin bên ngoài vẫn bị từ chối.

Các phần ổn định liên quan đã được giữ nguyên:

- PFM đã cập nhật 6 commit mới từ `origin/main` và tích hợp lại thay đổi STT không xung đột.
- `better-sqlite3` được khóa ở `12.11.1` để tránh native crash trên Windows; kiểm tra SQLite trả `integrity=ok`.
- Database, cấu hình local và các tính năng PFM mới không bị ghi đè.

## Bằng chứng kiểm tra

- `GET /health`: `200`, provider `phowhisper`, `configured=true`.
- `POST /api/stt/session`: `200`, trả WebSocket `ws://127.0.0.1:8000/api/v1/transcriptions/stream`.
- Kiểm tra live protocol: `ready → finishing → final` trong khoảng `0,54 giây` với luồng im lặng.
- Backend STT: `38/38` test đạt.
- PFM streaming: `16/16` test đạt.
- Baseline PFM sau khi đồng bộ remote: `447/447` test đạt và production build thành công.
- Kiểm tra origin live: `localhost=200`, `127.0.0.1=200`, origin ngoài hệ thống `403`.

Kiểm tra live tự động hiện dùng luồng im lặng vì repo chưa có WAV tiếng Việt mẫu. Trước khi go-live cần chạy thêm một ca microphone thực và một WAV PCM16 mono 16 kHz để ghi nhận nội dung, partial latency và final latency.

## Việc cần làm trước go-live

1. Tạo hai branch/PR riêng cho hai repo, ví dụ `feat/phowhisper-streaming` (STT) và `feat/stt-streaming-client` (PFM). Không đưa `.env`, API key hoặc log local vào commit.
2. Merge STT trước để CI test, build và publish image mới lên VCR bằng tag commit SHA.
3. Redeploy Agent Runtime bằng đúng image SHA mới. Việc publish image không tự restart runtime hiện tại.
4. Cấu hình Agent Runtime:

   ```dotenv
   STT_PROVIDER=phowhisper
   PHOWHISPER_MODEL_ID=vinai/PhoWhisper-base
   PHOWHISPER_DEVICE=-1
   PHOWHISPER_PRELOAD=true
   HF_HUB_OFFLINE=1
   SERVICE_API_KEY=<secret>
   STREAM_TOKEN_SECRET=<shared-secret-if-multiple-replicas>
   STREAM_MAX_SESSIONS=2
   ```

5. Gateway phải giữ header `Upgrade`/`Origin` và cho phép WebSocket sống ít nhất 180 giây.
6. Sau khi endpoint mới sẵn sàng, cấu hình PFM:

   ```dotenv
   STT_API_BASE_URL=https://<agent-runtime-endpoint>
   STT_SERVICE_API_KEY=<same-service-key>
   APP_ORIGIN=https://<public-pfm-origin>
   ```

7. Chạy smoke test public endpoint:

   ```powershell
   python scripts/check_streaming.py --base-url https://<agent-runtime-endpoint> --origin https://<public-pfm-origin>
   python scripts/check_streaming.py --base-url https://<agent-runtime-endpoint> --origin https://<public-pfm-origin> --wav sample-16k-mono.wav
   ```

8. Manual E2E trên PFM: mở M-Your, cấp quyền microphone, xác nhận partial/final text, dừng microphone, gửi câu hỏi và kiểm tra Agent trả lời.

## Rủi ro còn lại

- Endpoint GreenNode hiện tại vẫn dùng image cũ và chưa có streaming; phải redeploy image mới trước khi đổi PFM về endpoint này.
- PhoWhisper đang thực hiện inference lặp trên audio tích luỹ; đây là near-real-time streaming, không phải decoder streaming native.
- CPU có thể chậm vài giây ở final inference. `PHOWHISPER_PRELOAD=true` loại bỏ thời gian nạp model ở request đầu nhưng không loại bỏ thời gian inference.
- Cần dùng cùng `STREAM_TOKEN_SECRET` giữa các replica hoặc bảo đảm ticket quay lại đúng worker.
