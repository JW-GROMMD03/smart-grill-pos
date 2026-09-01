const API_URL = '/api/v1/auth';
let inactivityTimer;

function resetInactivityTimer() {
    clearTimeout(inactivityTimer);
    // 20 minutes = 1200000 ms
    inactivityTimer = setTimeout(logoutDueToInactivity, 1200000);
}

function logoutDueToInactivity() {
    localStorage.removeItem('sg_token');
    localStorage.removeItem('sg_user');
    alert("Session expired due to 20 minutes of inactivity. Please log in again.");
    window.location.href = '/index.html';
}

// Track activity across the document
['mousemove', 'keydown', 'click', 'scroll'].forEach(evt => 
    document.addEventListener(evt, resetInactivityTimer)
);

const loginForm = document.getElementById('loginForm');
if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const username = document.getElementById('username').value.trim();
        const pin = document.getElementById('pin').value.trim();
        
        const alertBox = document.getElementById('alertBox');
        const submitBtn = document.getElementById('submitBtn');
        const btnSpinner = document.getElementById('btnSpinner');
        const btnText = document.getElementById('btnText');

        // Reset UI to loading state
        alertBox.classList.add('hidden');
        submitBtn.disabled = true;
        btnSpinner.classList.remove('hidden');
        btnText.textContent = 'AUTHENTICATING...';

        try {
            const response = await fetch(`${API_URL}/cashier-login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, pin })
            });

            const data = await response.json();

            if (response.ok && (data.token || data.access_token)) {
                // Support both key names depending on backend response
                const token = data.token || data.access_token;
                
                localStorage.setItem('sg_token', token);
                localStorage.setItem('sg_user', JSON.stringify(data));
                
                // Prevent redirect loops by ensuring clean routing
                window.location.replace('/cashier-dashboard.html');
                
            } else {
                // Intelligent Error Parser
                let errorMsg = "Authentication Failed";
                if (data.detail) {
                    if (typeof data.detail === 'string') {
                        errorMsg = data.detail;
                    } else if (Array.isArray(data.detail)) {
                        errorMsg = data.detail.map(err => err.msg || JSON.stringify(err)).join(" | ");
                    } else if (typeof data.detail === 'object') {
                        errorMsg = data.detail.message || data.detail.error || JSON.stringify(data.detail);
                    }
                } else if (data.message) {
                    errorMsg = data.message;
                }

                // Handle specific Admin restrictions
                if (errorMsg.toLowerCase().includes("suspended")) {
                    errorMsg = "This account has been restricted by the Admin.";
                }
                
                alertBox.innerText = errorMsg;
                alertBox.classList.remove('hidden');
            }
        } catch (err) {
            alertBox.innerText = 'Server connection error.';
            alertBox.classList.remove('hidden');
        } finally {
            // Restore UI state
            submitBtn.disabled = false;
            btnSpinner.classList.add('hidden');
            btnText.textContent = 'SIGN IN TO POS TERMINAL';
        }
    });
}

// Initialize timer if logged in
if (localStorage.getItem('sg_token')) {
    resetInactivityTimer();
}