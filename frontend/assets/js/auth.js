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

document.getElementById('loginForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('username').value.trim();
    const pin = document.getElementById('pin').value.trim();
    const alertBox = document.getElementById('alertBox');

    try {
        const response = await fetch(`${API_URL}/cashier-login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, pin })
        });

        const data = await response.json();

        if (!response.ok) {
            // Handle specific Admin restrictions
            if (data.detail && data.detail.toLowerCase().includes("suspended")) {
                alertBox.innerText = "This account has been restricted by the Admin.";
            } else {
                alertBox.innerText = data.detail || 'Login failed.';
            }
            alertBox.classList.remove('hidden');
            return;
        }

        localStorage.setItem('sg_token', data.token);
        localStorage.setItem('sg_user', JSON.stringify(data));
        
        // Prevent redirect loops by ensuring clean routing
        window.location.replace('/cashier-dashboard.html');
        
    } catch (err) {
        alertBox.innerText = 'Server connection error.';
        alertBox.classList.remove('hidden');
    }
});

// Initialize timer if logged in
if (localStorage.getItem('sg_token')) {
    resetInactivityTimer();
}