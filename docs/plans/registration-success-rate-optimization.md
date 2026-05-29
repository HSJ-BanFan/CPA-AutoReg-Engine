# Batch Registration Success Rate Optimization Plan

**Goal**: Raise Camoufox serial batch registration from 68% (34/50) to 85-90%.

**Current failure profile** (16 failures from serial run):
- 11x "未找到注册按钮" — page load / element detection race
- 3x "未完成注册流程" — post-registration flow incomplete
- 1x "host 解析失败" — transient DNS
- 1x "未找到 OTP 输入框" — browser closed prematurely

---

## Phase 1: Page Load Hardening (target: eliminate "未找到注册按钮" failures)

### 1a. Cloudflare Challenge Detection & Wait

File: `src/core/openai/chatgpt_browser_client.py`

In `_open_signup_entry()`, before looking for signup button:
- After `page.goto(CHATGPT_BASE)`, add `wait_for_load_state("networkidle", timeout=15000)` then `time.sleep(2)` to settle
- Detect Cloudflare challenge page (`#challenge-form`, `cf-challenge`, `Checking your browser` text)
- If CF detected, wait up to 30s for it to pass, polling every 3s
- If CF fails after 30s, raise clear error (retry will trigger at engine level)

### 1b. Signup Entry Retry Loop

File: `src/core/openai/chatgpt_browser_client.py`

In `_open_signup_entry()`:
```python
for attempt in range(3):
    try:
        self._page.wait_for_selector(
            'button:has-text("Sign up")',
            state="visible",
            timeout=10000  # was 5000
        )
        break
    except Exception:
        if attempt < 2:
            self._log(f"Signup button not visible, retry {attempt+2}/3...")
            self._page.reload(wait_until="networkidle")
            time.sleep(3)
        else:
            raise BrowserAutomationError("未找到注册入口")
```

### 1c. Increase element timeouts

File: `src/core/openai/chatgpt_browser_client.py`

- `DEFAULT_TIMEOUT`: 15000 → 20000
- `PAGE_LOAD_TIMEOUT`: 45000 → 60000

Rationale: Cloudflare challenge + page render can take 10-15s on slow networks. Current 5s timeouts are too tight.

---

## Phase 2: Step-Level Retry in Worker (target: reduce full-flow restarts)

### 2a. Worker-Internal Retry

File: `src/core/camoufox_registration_worker.py`

In `run()`, wrap `client.register_complete_flow()` in a retry loop (max 2):
- On "未找到" / "element not found" / "timeout" errors → re-launch browser and retry from start of `register_complete_flow`
- On "验证码" / "registration_disallowed" → don't retry, fail immediately
- Re-use same email_adapter / email across retries (no new email creation)

This avoids the expensive engine-level retry that creates a new email each time.

### 2b. Engine-Level Retry Policy Update

File: `src/core/register_v2.py`

In `_should_retry()`, add:
```python
retriable_markers.extend([
    "未找到注册按钮",
    "未找到注册入口",
    "host 解析失败",
    "DNS",
    "connection reset",
    "page crash",
    "context was destroyed",
])
```
Remove from retriable: keep existing markers, these are just additions.

---

## Phase 3: OTP Polling Optimization (target: fewer timeouts on OTP wait)

### 3a. Adaptive Poll Intervals

File: `src/core/register_v2.py` → `EmailServiceAdapter.wait_for_verification_code()`

Change from fixed 8s poll window to adaptive:
```python
elapsed = time.time() - started
if elapsed < 15:
    poll_seconds = 2   # frequent polling when OTP likely arriving
elif elapsed < 40:
    poll_seconds = 5   # moderate
else:
    poll_seconds = 8   # spaced out when unlikely
```

### 3b. Increase Default OTP Timeout

File: `src/config/settings.py`

- `email_code_timeout` default: 30 → 45
- Retain the 180s hard cap via `MAX_VERIFICATION_TIMEOUT` (already in code)

---

## Phase 4: Inter-Account Timing Tune

File: `run_batch_camoufox_50.py`

- Reduce inter-account sleep from 5s → 3s (each account runs fresh browser+context, no collusion risk)
- Add random jitter: `time.sleep(random.uniform(2, 5))`

Won't affect success rate directly but saves ~100s per 50-account batch.

---

## Files to Change

| File | Phase | Changes |
|------|-------|---------|
| `src/core/openai/chatgpt_browser_client.py` | 1a, 1b, 1c | CF detection, signup retry, timeout bumps |
| `src/core/camoufox_registration_worker.py` | 2a | Worker-level retry wrapper |
| `src/core/register_v2.py` | 2b, 3a | Retry markers, adaptive OTP polling |
| `src/config/settings.py` | 3b | OTP timeout default |
| `run_batch_camoufox_50.py` | 4 | Sleep timing |

## Expected Results

| Phase | Before | After |
|-------|--------|-------|
| "未找到注册按钮" | 11/50 (22%) | 2-3/50 (4-6%) |
| "未完成注册流程" | 3/50 (6%) | 1/50 (2%) |
| OTP issues | 1/50 (2%) | 0/50 (0%) |
| **Overall success** | **34/50 (68%)** | **43-45/50 (86-90%)** |

## Rollback Strategy

Each phase is independent. If Phase 1 alone brings success rate to 85%+, skip Phase 2-3. If any phase causes regression, revert that single file via git.
