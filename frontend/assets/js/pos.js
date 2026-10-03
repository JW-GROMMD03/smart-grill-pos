// pos.js
const API_POS = '/api/v1/pos';

let cart = JSON.parse(localStorage.getItem('sg_cart')) || [];
let holdQueue = JSON.parse(localStorage.getItem('sg_holds')) || [];
let deletePollInterval = null;
let pendingItem = null;
let cashierWs = null;

document.addEventListener("DOMContentLoaded", () => {
  // Enforce Light Mode as default if no preference is saved
  if (!localStorage.getItem('sg_theme')) {
    localStorage.setItem('sg_theme', 'light');
  }
  applyTheme(localStorage.getItem('sg_theme'));

  // Attach theme toggle listener if button exists in DOM
  const themeBtn = document.getElementById('themeToggleBtn');
  if (themeBtn) {
    themeBtn.addEventListener('click', toggleAppTheme);
  }

  loadDynamicMenu();
  updateState(); 
  connectCashierSocket();
});

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'dark') {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
  localStorage.setItem('sg_theme', theme);

  // Update toggle icon if present
  const themeIcon = document.getElementById('themeIcon');
  if (themeIcon) {
    themeIcon.innerText = theme === 'dark' ? '☀️' : '🌙';
  }
}

function toggleAppTheme() {
  const current = localStorage.getItem('sg_theme') || 'light';
  const next = current === 'light' ? 'dark' : 'light';
  applyTheme(next);
}

function connectCashierSocket() {
    const token = localStorage.getItem('sg_token');
    const user = JSON.parse(localStorage.getItem('sg_user') || '{}');
    if (!token || !user.id) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/cashier/${user.id}?token=${token}`;
    
    cashierWs = new WebSocket(wsUrl);
    
    cashierWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.action === 'force_logout') {
            document.getElementById('lockoutReason').innerText = data.reason || "Your access has been revoked by the Executive Admin.";
            document.getElementById('shiftLockoutOverlay').classList.remove('hidden');
            document.getElementById('shiftLockoutOverlay').classList.add('flex');
            
            localStorage.removeItem('sg_token');
            localStorage.removeItem('sg_user');
            
            setTimeout(() => { window.location.replace('/index.html'); }, 6000);
        } else if (data.action === 'menu_refresh') {
            loadDynamicMenu();
        }
    };
    
    cashierWs.onclose = () => {
        setTimeout(connectCashierSocket, 5000);
    };
}

function getAuthToken() {
  return localStorage.getItem('sg_token') || '';
}

function saveState() {
  localStorage.setItem('sg_cart', JSON.stringify(cart));
  localStorage.setItem('sg_holds', JSON.stringify(holdQueue));
}

function updateState() {
  saveState();
  renderCart();
  renderHoldQueue();
  
  const badge = document.getElementById('mobileCartCount');
  if(badge) {
      const totalCount = cart.reduce((acc, item) => acc + item.quantity, 0);
      badge.innerText = totalCount;
  }
}

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  if (sidebar && overlay) {
    sidebar.classList.toggle('-translate-x-full');
    overlay.classList.toggle('hidden');
  }
}

async function loadDynamicMenu() {
  const container = document.getElementById('dynamicMenuGrid');
  try {
    const res = await fetch(`${API_POS}/menu`, {
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    
    if (res.status === 403 || res.status === 401) {
      alert("Shift locked out or session expired.");
      window.location.href = '/index.html';
      return;
    }

    if (!res.ok) throw new Error("Failed to load data from server");
    
    const items = await res.json();
    
    if (!items || !Array.isArray(items) || items.length === 0) {
        if(container) {
          container.innerHTML = `
            <div class="col-span-2 md:col-span-3 xl:col-span-5 flex flex-col items-center justify-center py-12 px-4 text-center bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-xs">
              <span class="text-4xl mb-3">🍽️</span>
              <p class="font-extrabold text-amber-600 dark:text-amber-400 text-sm">The catalog is currently empty.</p>
              <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Waiting for the Admin to add and activate menu items.</p>
            </div>
          `;
        }
        return;
    }

    renderMenuGrid(items);
  } catch (e) {
    console.error("Failed to load dynamic menu", e);
    if(container) {
      container.innerHTML = `
        <div class="col-span-2 md:col-span-3 xl:col-span-5 flex flex-col items-center justify-center py-12 px-4 text-center bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 rounded-2xl">
          <span class="text-4xl mb-3">⚠️</span>
          <p class="font-extrabold text-red-600 dark:text-red-400 text-sm">Network Error.</p>
          <p class="text-xs text-slate-600 dark:text-slate-400 mt-2">The system encountered an error connecting to the database. Please check your connection and refresh.</p>
          <button onclick="location.reload()" class="mt-4 px-4 py-2 bg-slate-900 dark:bg-slate-800 text-white text-xs font-bold rounded hover:bg-slate-800 dark:hover:bg-slate-700">Reload Menu</button>
        </div>
      `;
    }
  }
}

function renderMenuGrid(items) {
  const container = document.getElementById('dynamicMenuGrid');
  if(!container) return;

  items = items.filter(i => i.is_active === true);

  const getCat = (cat) => items.filter(i => i.category === cat).sort((a,b) => a.price - b.price);
  const tilapia = getCat('TILAPIA VARIATIONS');
  const mbuta = getCat('MBUTA VARIATIONS');
  const wetfry = getCat('WETFRY');
  const greens = getCat('GREENS & KACHUMBARI');
  const drinks = getCat('DRINKS & WATER');
  const chips = getCat('CHIPS & PACKAGING');
  const mukimo = getCat('MUKIMO / MATAHA');
  const ugali = getCat('UGALI');
  const tea = getCat('TEA');
  const others = getCat('OTHERS');
  
  const mbuzi = items.filter(i => i.category === 'MEAT CUTS' && i.name.toLowerCase().includes('mbuzi')).sort((a,b) => a.price - b.price);
  const beef = items.filter(i => i.category === 'MEAT CUTS' && i.name.toLowerCase().includes('beef')).sort((a,b) => a.price - b.price);
  const chicken = items.filter(i => i.category === 'MEAT CUTS' && i.name.toLowerCase().includes('chicken')).sort((a,b) => a.price - b.price);
  const boneSoup = items.filter(i => i.category === 'MEAT CUTS' && (i.name.toLowerCase().includes('bone soup') || i.sub_category === 'bone_soup')).sort((a,b) => a.price - b.price);

  const blockBtn = (i) => `<button onclick="triggerQuantityModal('${i.name}', '${i.category}', ${i.price})" class="bg-white hover:bg-slate-50 dark:bg-slate-900 dark:hover:bg-slate-800 border border-slate-200 dark:border-slate-800 rounded-xl p-3 text-left flex flex-col justify-between shadow-2xs transition"><span class="text-slate-900 dark:text-slate-200 text-xs font-bold">${i.name}</span><span class="text-amber-600 dark:text-amber-400 text-xs font-black mt-2">${i.price}/=</span></button>`;
  const inlineBtn = (i) => `<button onclick="triggerQuantityModal('${i.name}', '${i.category}', ${i.price})" class="bg-white hover:bg-slate-50 dark:bg-slate-900 dark:hover:bg-slate-800 border border-slate-200 dark:border-slate-800 rounded-lg p-2 text-center transition shadow-2xs"><span class="text-amber-600 dark:text-amber-400 text-[11px] font-bold">${i.price}/=</span></button>`;

  container.innerHTML = `
    <div class="mb-5">
       <h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5 flex items-center gap-1.5"><span>🐟</span> TILAPIA VARIATIONS</h3>
       <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2.5">${tilapia.map(blockBtn).join('')}</div>
    </div>
    <div class="mb-5">
       <h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5 flex items-center gap-1.5"><span>🐟</span> MBUTA VARIATIONS</h3>
       <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2.5">${mbuta.map(blockBtn).join('')}</div>
    </div>
    <div class="mb-5">
       <h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5 flex items-center gap-1.5"><span>🥩</span> MEAT CUTS (WITH KG SPECIFICATION)</h3>
       <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div class="bg-white dark:bg-slate-900 p-3 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xs"><p class="text-slate-900 dark:text-slate-200 text-xs font-extrabold mb-2.5">Mbuzi</p><div class="grid grid-cols-3 gap-2">${mbuzi.map(inlineBtn).join('')}</div></div>
          <div class="bg-white dark:bg-slate-900 p-3 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xs"><p class="text-slate-900 dark:text-slate-200 text-xs font-extrabold mb-2.5">Beef</p><div class="grid grid-cols-3 gap-2">${beef.map(inlineBtn).join('')}</div></div>
          <div class="bg-white dark:bg-slate-900 p-3 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xs"><p class="text-slate-900 dark:text-slate-200 text-xs font-extrabold mb-2.5">Chicken</p><div class="grid grid-cols-3 gap-2">${chicken.map(inlineBtn).join('')}</div></div>
          <div class="bg-white dark:bg-slate-900 p-3 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xs"><p class="text-slate-900 dark:text-slate-200 text-xs font-extrabold mb-2.5">Bone Soup</p><div class="grid grid-cols-3 gap-2">${boneSoup.map(inlineBtn).join('')}</div></div>
       </div>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-5">
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🍲</span> WETFRY</h3><div class="grid grid-cols-3 gap-2.5">${wetfry.map(blockBtn).join('')}</div></div>
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🥬</span> GREENS & KACHUMBARI</h3><div class="grid grid-cols-2 gap-2.5">${greens.map(blockBtn).join('')}</div></div>
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🥔</span> MUKIMO / MATAHA</h3><div class="grid grid-cols-2 gap-2.5">${mukimo.map(blockBtn).join('')}</div></div>
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🍛</span> UGALI & TEA</h3><div class="grid grid-cols-2 gap-2.5">${ugali.map(blockBtn).join('')} ${tea.map(blockBtn).join('')}</div></div>
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🥣</span> OTHERS</h3><div class="grid grid-cols-2 gap-2.5">${others.map(blockBtn).join('')}</div></div>
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🥤</span> DRINKS & WATER</h3><div class="grid grid-cols-2 gap-2.5">${drinks.map(blockBtn).join('')}</div></div>
        <div><h3 class="text-amber-600 dark:text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2.5"><span>🍟</span> CHIPS & PACKAGING</h3><div class="grid grid-cols-3 gap-2.5">${chips.map(blockBtn).join('')}</div></div>
    </div>
  `;
}

function triggerQuantityModal(name, category, price) {
  pendingItem = { name, category, price };
  let displayName = name;
  if (category === 'MEAT CUTS') {
      let kgLabel = "1 KG";
      if (price <= 275 || (price === 200)) kgLabel = "1/4 KG";
      else if (price <= 600 || price === 350) kgLabel = "1/2 KG";
      displayName = `${name} (${kgLabel}) (${price}/=)`;
  }
  document.getElementById('qtyItemName').innerText = displayName;
  document.getElementById('qtySelect').value = "1";
  toggleModal('qtyModal');
}

function confirmQtyAdd() {
  const qty = parseInt(document.getElementById('qtySelect').value);
  if (pendingItem && qty > 0) {
    addToCart(pendingItem.name, pendingItem.category, pendingItem.price, qty);
  }
  toggleModal('qtyModal');
}

function addToCart(name, category, price, qty = 1) {
  let displayName = name;
  if(category === 'MEAT CUTS' && !name.includes('(')) {
    let kgLabel = "1 KG";
    if (price <= 275) kgLabel = "1/4 KG";
    else if (price <= 600) kgLabel = "1/2 KG";
    displayName = `${name} [${kgLabel}] (${price}/=)`; 
  }

  const existing = cart.find(i => i.item_name === displayName && i.unit_price === price);
  if (existing) {
    existing.quantity += qty;
    existing.subtotal = existing.quantity * existing.unit_price;
  } else {
    cart.push({ item_name: displayName, category: category, unit_price: price, quantity: qty, subtotal: price * qty });
  }
  updateState();
}

function renderCart() {
  const container = document.getElementById('cartList');
  if (cart.length === 0) {
    container.innerHTML = `<p class="text-slate-400 text-xs text-center py-10">No items selected yet.</p>`;
    document.getElementById('cartTotal').innerText = '0.00';
    validatePaymentInputs();
    return;
  }

  let grandTotal = 0;
  container.innerHTML = cart.map((item, index) => {
    grandTotal += item.subtotal;
    return `
      <div class="flex justify-between items-center bg-white dark:bg-slate-900 p-3 rounded-xl border border-slate-200 dark:border-slate-800 text-xs mb-2 shadow-2xs">
        <div><p class="font-bold text-slate-900 dark:text-slate-200">${item.item_name}</p><p class="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5">${item.quantity} x KSh ${item.unit_price}</p></div>
        <div class="flex items-center gap-3"><span class="font-bold text-amber-600 dark:text-amber-400">KSh ${item.subtotal}</span><button onclick="requestAdminAction('cart_remove', '${index}')" class="text-red-600 dark:text-red-400 font-bold px-2 py-1 bg-red-50 dark:bg-red-500/10 rounded-lg hover:bg-red-100 transition">✕</button></div>
      </div>
    `;
  }).join('');
  document.getElementById('cartTotal').innerText = grandTotal.toFixed(2);
  validatePaymentInputs();
}

function selectPaymentMethod(method) {
  document.getElementById('paymentMethod').value = method;
  const partialFields = document.getElementById('partialFields');
  if (method === 'partial') partialFields.classList.remove('hidden');
  else partialFields.classList.add('hidden');
  
  document.querySelectorAll('.pay-btn').forEach(btn => btn.classList.remove('ring-2', 'ring-amber-500'));
  document.getElementById(`btn-${method}`).classList.add('ring-2', 'ring-amber-500');
  validatePaymentInputs();
}

function validatePaymentInputs() {
  const method = document.getElementById('paymentMethod').value;
  const total = parseFloat(document.getElementById('cartTotal').innerText) || 0;
  const checkoutBtn = document.getElementById('checkoutBtn');
  const errorMsg = document.getElementById('paymentError');

  if (cart.length === 0 || total === 0) {
    checkoutBtn.disabled = true;
    errorMsg.classList.add('hidden');
    return;
  }

  if (method === 'partial') {
    const cash = parseFloat(document.getElementById('cashInput').value) || 0;
    const mpesa = parseFloat(document.getElementById('mpesaInput').value) || 0;
    const tally = cash + mpesa;
    if (Math.abs(tally - total) > 0.01) {
      checkoutBtn.disabled = true;
      errorMsg.innerText = `Cash + M-Pesa (${tally}) must equal Total (${total}).`;
      errorMsg.classList.remove('hidden');
    } else {
      checkoutBtn.disabled = false;
      errorMsg.classList.add('hidden');
    }
  } else {
    checkoutBtn.disabled = false;
    errorMsg.classList.add('hidden');
  }
}

// =========================================================================
// REAL HARDWARE PRINTER DETECTION & CHECKOUT FLOW (BYPASSED ON MOBILE)
// =========================================================================
async function checkHardwarePrinterConnection() {
    const savedConfig = localStorage.getItem('sg_printer_config');
    if (!savedConfig) return { connected: false, reason: "No printer configuration saved." };

    try {
        const cfg = JSON.parse(savedConfig);
        if (cfg.interface === 'USB' && navigator.serial) {
            const ports = await navigator.serial.getPorts();
            if (ports && ports.length > 0) {
                return { connected: true, type: 'USB' };
            }
        }
        
        if (localStorage.getItem('sg_printer_verified') === 'true') {
            return { connected: true, type: cfg.interface || 'SIMULATED' };
        }

        return { connected: false, reason: "No active physical printer port detected." };
    } catch (e) {
        return { connected: false, reason: e.message };
    }
}

async function submitOrder() {
  // STRICT MOBILE DETECTION: Skip printer checks & receipt printing entirely on phones/tablets
  const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 1024;

  if (isMobile) {
      // Record order directly without requesting receipt printing or printer verification
      await executeOrderSubmission(false, false);
      return;
  }

  const printerConfig = localStorage.getItem('sg_printer_config');
  if (!printerConfig) {
      if (confirm("⚠️ No printer configured! Would you like to configure your printer now?")) {
          toggleModal('printerModal');
      }
      return;
  }

  const hardwareStatus = await checkHardwarePrinterConnection();
  executeOrderSubmission(true, hardwareStatus.connected);
}

async function executeOrderSubmission(printReceiptFlag, hardwareVerifiedFlag) {
  const total = parseFloat(document.getElementById('cartTotal').innerText);
  const method = document.getElementById('paymentMethod').value;
  const user = JSON.parse(localStorage.getItem('sg_user') || '{}');

  const payload = {
    cashier_id: user.id || "00000000-0000-0000-0000-000000000000",
    payment_method: method,
    cash_amount: method === 'partial' ? parseFloat(document.getElementById('cashInput').value) : (method === 'cash' ? total : 0),
    mpesa_amount: method === 'partial' ? parseFloat(document.getElementById('mpesaInput').value) : (method === 'mpesa' ? total : 0),
    total_amount: total,
    items: cart,
    print_receipt: printReceiptFlag,
    printer_hardware_verified: hardwareVerifiedFlag
  };

  try {
    const res = await fetch(`${API_POS}/checkout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      const dataRes = await res.json();
      
      if (printReceiptFlag) {
          alert("Order Processed Successfully & Printing Receipt!");
          printBranchReceipt(payload, dataRes.order_id);
      } else {
          alert("Order Recorded Successfully!");
      }

      cart = [];
      updateState();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail}`);
    }
  } catch (e) {
    alert("Network error processing sale.");
  }
}

// =========================================================================
// COMPACT RESTAURANT THERMAL RECEIPT GENERATOR (PAPER-SAVING + LOGO)
// =========================================================================
function printBranchReceipt(orderPayload, orderId) {
  const user = JSON.parse(localStorage.getItem('sg_user') || '{}');
  const branchName = (user.branch || localStorage.getItem('cashier_branch') || 'Smartgrill').trim();
  const lowerBranch = branchName.toLowerCase();

  let contactLines = "";
  let paymentInfoLines = "";

  if (lowerBranch.includes("smartgrill") || lowerBranch.includes("smart grill")) {
    contactLines = "Tel: 0700041003 / 0759960035<br>Email: smartgrill2026@gmail.com";
    paymentInfoLines = "M-Pesa Till No: <strong>4325536</strong>";
  } else if (lowerBranch.includes("nyama villa")) {
    contactLines = "Tel: 0700041003 / 0140 139 181";
    paymentInfoLines = "Pochi la Biashara: <strong>0140 139 181</strong>";
  } else if (lowerBranch.includes("smart kitchen")) {
    contactLines = "Tel: 0700-041003 / 0104-041003";
    paymentInfoLines = ""; 
  }

  const itemsHtml = orderPayload.items.map((i, idx) => `
    <tr>
      <td colspan="2" style="padding-top: 2px; font-weight: bold; text-align: left; word-break: break-all;">${idx + 1}. ${i.item_name}</td>
    </tr>
    <tr>
      <td style="padding-bottom: 2px; text-align: left; color: #000; font-size: 10px; padding-left: 5px;">
        ${i.quantity} @ ${i.unit_price.toFixed(2)}
      </td>
      <td style="padding-bottom: 2px; text-align: right; font-weight: bold; font-size: 11px;">
        ${i.subtotal.toFixed(2)}
      </td>
    </tr>
  `).join('');

  const verificationData = JSON.stringify({
    receipt_no: String(orderId).toUpperCase(),
    branch: branchName,
    time: new Date().toLocaleString(),
    items: orderPayload.items.map(i => `${i.quantity}x ${i.item_name}`)
  });

  const receiptWindow = window.open('', '_blank', 'width=350,height=600');
  receiptWindow.document.write(`
    <!DOCTYPE html>
    <html>
      <head>
        <title>Receipt - ${branchName}</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"><\/script>
        <style>
          @page { margin: 0; }
          body { 
            font-family: 'Courier New', Courier, monospace; 
            color: #000; 
            background: #fff; 
            margin: 0; 
            padding: 0; 
            width: 80mm; 
            font-size: 11px;
          }
          .receipt-container {
            width: 100%;
            padding: 2mm 3mm;
            box-sizing: border-box;
          }
          .center { text-align: center; }
          .title { font-weight: bold; font-size: 14px; text-transform: uppercase; margin-bottom: 1px; }
          .branch { font-size: 11px; font-weight: bold; text-transform: uppercase; margin-bottom: 2px; }
          .divider { border-top: 1px dashed #000; margin: 3px 0; }
          table { width: 100%; border-collapse: collapse; margin-top: 2px; }
          .totals-table { width: 100%; margin-top: 2px; font-size: 11px; }
          .totals-table td { padding: 1px 0; }
          .qr-container { text-align: center; margin: 4px 0 2px 0; }
          .qr-box { display: inline-block; }
          .footer { margin-top: 4px; font-size: 9px; text-align: center; }
          
          .no-print { margin-bottom: 8px; padding: 6px; background: #f1f5f9; text-align: center; }
          .no-print button { padding: 4px 10px; background: #0f172a; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 11px;}
          
          @media print {
            .no-print { display: none !important; }
            body { width: 100%; } 
            .receipt-container { padding: 1mm 2mm; } 
          }
        </style>
      </head>
      <body>
        <div class="no-print">
          <button onclick="window.print()">🖨 Print Receipt</button>
        </div>

        <div class="receipt-container">
          <div class="center">
            <div style="font-size: 18px; margin-bottom: 1px;">🔥</div>
            <div class="title">SMART GRILL POS</div>
            <div class="branch">${branchName}</div>
            <div style="font-size: 9px;">${contactLines}</div>
            <div style="font-size: 10px; margin-top: 1px; font-weight: bold;">${paymentInfoLines}</div>
          </div>
          
          <div class="divider"></div>
          <div style="font-size: 10px; line-height: 1.2;">
            <div style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Receipt #: <strong>${String(orderId).toUpperCase()}</strong></div>
            <div>Date/Time: ${new Date().toLocaleString()}</div>
            <div>Cashier: ${user.full_name || 'Staff'}</div>
          </div>
          
          <div class="divider"></div>
          <table>
            <thead>
              <tr style="border-bottom: 1px solid #000; font-size: 10px;">
                <th style="text-align: left; padding-bottom: 1px;">DESCRIPTION</th>
                <th style="text-align: right; padding-bottom: 1px;">AMT</th>
              </tr>
            </thead>
            <tbody>
              ${itemsHtml}
            </tbody>
          </table>

          <div class="divider"></div>
          <table class="totals-table">
            <tr>
              <td style="text-align: left;">ITEMS COUNT:</td>
              <td style="text-align: right; font-weight: bold;">${orderPayload.items.reduce((acc, i) => acc + i.quantity, 0)}</td>
            </tr>
            <tr style="font-weight: bold; font-size: 12px; border-top: 1px dashed #000; border-bottom: 1px dashed #000;">
              <td style="padding: 2px 0;">TOTAL DUE:</td>
              <td style="text-align: right; padding: 2px 0;">KSh ${orderPayload.total_amount.toFixed(2)}</td>
            </tr>
            <tr>
              <td style="padding-top: 2px;">PAID VIA:</td>
              <td style="text-align: right; text-transform: uppercase; padding-top: 2px; font-weight: bold;">${orderPayload.payment_method}</td>
            </tr>
          </table>

          <div class="qr-container">
            <div class="qr-box" id="receiptQrCode"></div>
          </div>

          <div class="footer">
            <p style="margin: 1px 0; font-weight: bold;">THANK YOU FOR DINING WITH US!</p>
            <p style="margin: 1px 0; font-size: 8px;">Goods once sold are not returnable.</p>
            <div style="margin-top: 2px; font-size: 8px; font-weight: bold;">HAVYN TECH SOLUTIONS</div>
          </div>
        </div>

        <script>
          window.onload = function() {
            try {
              new QRCode(document.getElementById("receiptQrCode"), {
                text: ${JSON.stringify(verificationData)},
                width: 50,
                height: 50,
                colorDark: "#000000",
                colorLight: "#ffffff",
                correctLevel: QRCode.CorrectLevel.L
              });
            } catch(e) {}
            
            setTimeout(() => { window.print(); }, 400);
          }
        </script>
      </body>
    </html>
  `);
  receiptWindow.document.close();
}

function holdCurrentOrder() {
  if (cart.length === 0) return alert("Cart is empty.");
  if (holdQueue.length >= 6) return alert("Hold queue is full (Max 6 allowed).");
  
  const total = document.getElementById('cartTotal').innerText;
  holdQueue.push({ id: Date.now(), items: [...cart], total: total, time: new Date().toLocaleTimeString() });
  cart = [];
  updateState();
  alert("Order placed on hold.");
}

function renderHoldQueue() {
  const container = document.getElementById('holdList');
  if(!container) return;
  
  if (holdQueue.length === 0) {
    container.innerHTML = `<p class="text-slate-400 text-xs text-center py-6">No held orders.</p>`;
    return;
  }

  container.innerHTML = holdQueue.map((order, index) => `
    <div class="flex justify-between items-center bg-white dark:bg-slate-900 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs shadow-2xs">
      <div>
        <p class="font-bold text-amber-600 dark:text-amber-400">Hold #${index + 1} <span class="text-slate-400 font-normal ml-2">${order.time}</span></p>
        <p class="text-slate-700 dark:text-slate-300 mt-1">KSh ${order.total} (${order.items.length} items)</p>
      </div>
      <div class="flex gap-2">
        <button onclick="resumeHold(${index})" class="text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10 px-2.5 py-1.5 rounded-lg font-bold hover:bg-amber-100 transition">Resume</button>
        <button onclick="requestAdminAction('hold', '${order.id}')" class="text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10 px-2.5 py-1.5 rounded-lg font-bold hover:bg-red-100 transition">Drop</button>
      </div>
    </div>
  `).join('');
}

function resumeHold(index) {
  if(cart.length > 0) return alert("Please clear or hold the current cart first.");
  cart = holdQueue[index].items;
  holdQueue.splice(index, 1);
  updateState();
}

async function submitExpense(e) {
  e.preventDefault();
  const desc = document.getElementById('expDesc').value.trim();
  const amt = parseFloat(document.getElementById('expAmt').value);
  const type = document.getElementById('expType').value;

  if (amt > 1000) return alert("Error: Single expenses cannot exceed 1000 KSh.");
  
  try {
    const res = await fetch(`${API_POS}/expense`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify({ description: desc, amount: amt, payment_type: type })
    });
    
    if(res.ok) { 
      alert("Expense logged successfully."); 
      document.getElementById('expDesc').value = '';
      document.getElementById('expAmt').value = '';
      toggleModal('expensesModal');
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail}`);
    }
  } catch (e) {
    alert("Error connecting to server to log expense.");
  }
}

async function loadReceipts() {
  try {
    const res = await fetch(`${API_POS}/my-sales`, { 
      headers: { 'Authorization': `Bearer ${getAuthToken()}` } 
    });
    const data = await res.json();
    const container = document.getElementById('receiptsList');
    
    let html = `<h4 class="text-xs font-bold text-slate-500 dark:text-slate-400 mb-2 border-b border-slate-200 dark:border-slate-800 pb-1">SALES (${data.transactions.length})</h4>`;
    if (data.transactions.length === 0) html += `<p class="text-slate-400 text-xs mb-4">No sales recorded yet.</p>`;
    
    html += data.transactions.map(t => `
      <div class="bg-white dark:bg-slate-900 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 mb-2 text-xs flex justify-between items-center shadow-2xs">
        <div>
          <span class="font-bold text-slate-900 dark:text-slate-200">Sale #${t.id.split('-')[0]}</span>
          <p class="text-slate-600 dark:text-slate-400 mt-0.5">KSh ${t.total_amount} <span class="uppercase bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[9px] ml-1 text-slate-700 dark:text-slate-400">${t.payment_method}</span></p>
        </div>
        <button onclick="requestAdminAction('sale', '${t.id}')" class="text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10 px-2.5 py-1 rounded-lg font-bold hover:bg-red-100 transition">Delete</button>
      </div>
    `).join('');
    
    html += `<h4 class="text-xs font-bold text-slate-500 dark:text-slate-400 mt-6 mb-2 border-b border-slate-200 dark:border-slate-800 pb-1">EXPENSES (${data.expenses.length})</h4>`;
    if (data.expenses.length === 0) html += `<p class="text-slate-400 text-xs">No expenses recorded yet.</p>`;
    
    html += data.expenses.map(ex => `
      <div class="bg-white dark:bg-slate-900 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 mb-2 text-xs flex justify-between items-center shadow-2xs">
        <div>
          <span class="font-bold text-slate-900 dark:text-slate-200">${ex.description}</span>
          <p class="text-red-600 dark:text-red-400 mt-0.5 font-bold">- KSh ${ex.amount} <span class="uppercase bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[9px] ml-1 text-slate-600 dark:text-slate-400">${ex.payment_type}</span></p>
        </div>
        <button onclick="requestAdminAction('expense_delete', '${ex.id}')" class="text-slate-600 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-2.5 py-1 rounded-lg font-bold hover:text-red-600 transition">Delete</button>
      </div>
    `).join('');
    
    container.innerHTML = html;
  } catch(e) {
    console.error("Failed to load receipts.");
  }
}

async function executeExpenseDelete(id) {
  try {
    const res = await fetch(`${API_POS}/expense/${id}`, { 
      method: 'DELETE', 
      headers: { 'Authorization': `Bearer ${getAuthToken()}` } 
    });
    if(res.ok) { loadReceipts(); } else { alert("Failed to delete expense."); }
  } catch(e) { alert("Network error."); }
}

async function requestAdminAction(actionType, targetId) {
  if (actionType === 'cart_clear' && cart.length === 0) return;
  const user = JSON.parse(localStorage.getItem('sg_user') || '{}');
  
  try {
    const res = await fetch(`${API_POS}/request-delete-qr`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify({ target_id: targetId.toString(), cashier_id: user.id || "00000000-0000-0000-0000-000000000000" })
    });

    if (!res.ok) {
      const err = await res.json();
      alert(`Authorization request failed: ${err.detail || 'Invalid data'}`);
      return;
    }

    const data = await res.json();
    if (data.status === 'success') {
      openAdminModal(data.qr_token, data.short_code, actionType, targetId);
    }
  } catch (e) {
    alert("Could not request authorization.");
  }
}

function openAdminModal(token, shortCode, actionType, targetId) {
  const modal = document.getElementById('adminModal');
  const qrContainer = document.getElementById('adminQrCode');
  
  modal.classList.remove('hidden');
  qrContainer.innerHTML = '';

  new QRCode(qrContainer, {
    text: `smartgrill://approve-delete?token=${token}`,
    width: 150,
    height: 150
  });
  
  document.getElementById('adminShortCode').innerText = shortCode;

  deletePollInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API_POS}/check-delete-status/${token}`, {
        headers: { 'Authorization': `Bearer ${getAuthToken()}` }
      });
      const data = await res.json();

      if (data.status === 'approved') {
        clearInterval(deletePollInterval);
        modal.classList.add('hidden');
        
        if (actionType === 'hold') {
          holdQueue = holdQueue.filter(h => h.id != targetId);
          updateState();
        } else if (actionType === 'sale') {
          loadReceipts();
        } else if (actionType === 'cart_remove') {
          cart.splice(parseInt(targetId), 1);
          updateState();
        } else if (actionType === 'cart_clear') {
          cart = [];
          updateState();
        } else if (actionType === 'expense_delete') {
          executeExpenseDelete(targetId);
        }
        
        alert("Action Authorized by Admin!");
      } else if (data.status === 'expired') {
        clearInterval(deletePollInterval);
        modal.classList.add('hidden');
        alert("Authorization request expired.");
      }
    } catch(e) {
      console.error("Polling error");
    }
  }, 2000);
}

function toggleModal(id) {
  const modal = document.getElementById(id);
  modal.classList.toggle('hidden');
  
  if (id === 'receiptsModal' && !modal.classList.contains('hidden')) {
    loadReceipts();
  }
}