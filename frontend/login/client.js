// Redirect if already logged in
const token = localStorage.getItem('token');
if (token) window.location.replace('/home/index.html');

/* ── UI helpers ─────────────────────────────────────── */
function revealOTP() {
    document.getElementById('otpSection').style.display = 'block';
    document.getElementById('sendBtn').style.display    = 'none';
    document.getElementById('resendBtn').style.display  = 'flex';
    document.getElementById('email').readOnly           = true;
    document.getElementById('otp').focus();
}

let statusTimer = null;
function setStatus(msg, type, autoClear) {
    const s = document.getElementById('status');
    clearTimeout(statusTimer);
    s.classList.remove('visible');
    setTimeout(() => {
        s.innerText  = msg;
        s.className  = type === 'error' ? 'error' : '';
        s.offsetHeight; // force reflow
        s.classList.add('visible');
        if (autoClear) {
            statusTimer = setTimeout(() => {
                s.classList.remove('visible');
                setTimeout(() => { s.innerText = ''; s.className = ''; }, 250);
            }, 3000);
        }
    }, 150);
}

function flashError(inputId) {
    const el = document.getElementById(inputId);
    el.classList.add('error');
    setTimeout(() => el.classList.remove('error'), 2000);
}

/* ── API calls ──────────────────────────────────────── */
async function sendOTP() {
    const email = document.getElementById('email').value.trim();
    if (!email) {
        flashError('email');
        setStatus('Enter an email first', 'error', true);
        return;
    }

    const res  = await fetch('/auth/request-otp', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ email })
    });
    const data = await res.json();

    if (res.ok) {
        revealOTP();
        setStatus('OTP sent', '', true);
    } else {
        flashError('email');
        setStatus(data.error || 'Failed to send OTP', 'error', true);
    }
}

async function verifyOTP() {
    const email = document.getElementById('email').value.trim();
    const otp   = document.getElementById('otp').value.trim();

    if (!otp) {
        flashError('otp');
        setStatus('Enter the OTP', 'error', true);
        return;
    }
    if (otp.length !== 6) {
        flashError('otp');
        setStatus('Must be 6 digits', 'error', true);
        return;
    }

    const res  = await fetch('/auth/verify-otp', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ email, otp })
    });
    const data = await res.json();

    if (data.success) {
        localStorage.setItem('token', data.token);
        setStatus('Verified! Redirecting...', '', false);
        setTimeout(() => { window.location.replace('/home/index.html'); }, 800);
    } else {
        flashError('otp');
        setStatus(data.error || 'Invalid OTP', 'error', true);
    }
}