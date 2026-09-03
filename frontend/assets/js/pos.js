const API_POS = '/api/v1/pos';

// State Persistence via LocalStorage
let cart = JSON.parse(localStorage.getItem('sg_cart')) || [];
let holdQueue = JSON.parse(localStorage.getItem('sg_holds')) || [];
let deletePollInterval = null;
let pendingItem = null;
let cashierWs = null;

document.addEventListener("DOMContentLoaded", () => {
  loadDynamicMenu();
  updateState(); 
  connectCashierSocket();
});

// ==========================================
// REAL-TIME SESSION MANAGEMENT
// ==========================================
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
            
            // Purge credentials immediately
            localStorage.removeItem('sg_token');
            localStorage.removeItem('sg_user');
            
            // Redirect after 6 seconds to ensure the cashier reads the message
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
    
    // Graceful handling of empty menu state without throwing a console error
    if (!items || !Array.isArray(items) || items.length === 0) {
        if(container) {
          container.innerHTML = `
            <div class="col-span-2 md:col-span-3 xl:col-span-5 flex flex-col items-center justify-center py-12 px-4 text-center bg-slate-800/50 border border-slate-700/50 rounded-2xl">
              <span class="text-4xl mb-3">🍽️</span>
              <p class="font-extrabold text-amber-400 text-sm">The catalog is currently empty.</p>
              <p class="text-xs text-slate-400 mt-2">Waiting for the Admin to add and activate menu items.</p>
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
        <div class="col-span-2 md:col-span-3 xl:col-span-5 flex flex-col items-center justify-center py-12 px-4 text-center bg-red-500/10 border border-red-500/20 rounded-2xl">
          <span class="text-4xl mb-3">⚠️</span>
          <p class="font-extrabold text-red-400 text-sm">Network Error.</p>
          <p class="text-xs text-slate-400 mt-2">The system encountered an error connecting to the database. Please check your connection and refresh.</p>
          <button onclick="location.reload()" class="mt-4 px-4 py-2 bg-slate-800 text-slate-200 text-xs font-bold rounded hover:bg-slate-700">Reload Menu</button>
        </div>
      `;
    }
  }
}

function renderMenuGrid(items) {
  const container = document.getElementById('dynamicMenuGrid');
  if(!container) return;

  // Force the POS to ignore disabled items, protecting it from Admin cache pollution
  items = items.filter(i => i.is_active === true);

  const getCat = (cat) => items.filter(i => i.category === cat).sort((a,b) => a.price - b.price);
  const tilapia = getCat('TILAPIA VARIATIONS');
  const wetfry = getCat('WETFRY');
  const greens = getCat('GREENS & KACHUMBARI');
  const drinks = getCat('DRINKS & WATER');
  const chips = getCat('CHIPS & PACKAGING');
  
  const mbuzi = items.filter(i => i.category === 'MEAT CUTS' && i.name.includes('Mbuzi')).sort((a,b) => a.price - b.price);
  const beef = items.filter(i => i.category === 'MEAT CUTS' && i.name.includes('Beef')).sort((a,b) => a.price - b.price);
  const chicken = items.filter(i => i.category === 'MEAT CUTS' && i.name.includes('Chicken')).sort((a,b) => a.price - b.price);

  const blockBtn = (i) => `<button onclick="triggerQuantityModal('${i.name}', '${i.category}', ${i.price})" class="bg-slate-900/60 hover:bg-slate-800 border border-slate-700/50 rounded-lg p-2.5 text-left flex flex-col justify-between shadow-sm transition"><span class="text-slate-200 text-[11px] font-bold">${i.name}</span><span class="text-amber-400 text-xs font-black mt-1">${i.price}/=</span></button>`;
  const inlineBtn = (i) => `<button onclick="triggerQuantityModal('${i.name}', '${i.category}', ${i.price})" class="bg-slate-900 hover:bg-slate-800 border border-slate-700 rounded p-1.5 text-center transition"><span class="text-amber-400 text-[10px] font-bold">${i.price}/=</span></button>`;

  container.innerHTML = `
    <div class="mb-5">
       <h3 class="text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2 flex items-center gap-1"><span>🐟</span> TILAPIA VARIATIONS</h3>
       <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
         ${tilapia.map(blockBtn).join('')}
       </div>
    </div>

    <div class="mb-5">
       <h3 class="text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2 flex items-center gap-1"><span>🥩</span> MEAT CUTS</h3>
       <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div class="bg-slate-900/40 p-2.5 rounded-xl border border-slate-800">
             <p class="text-slate-200 text-xs font-bold mb-2">Mbuzi</p>
             <div class="grid grid-cols-3 gap-1.5">${mbuzi.map(inlineBtn).join('')}</div>
          </div>
          <div class="bg-slate-900/40 p-2.5 rounded-xl border border-slate-800">
             <p class="text-slate-200 text-xs font-bold mb-2">Beef</p>
             <div class="grid grid-cols-3 gap-1.5">${beef.map(inlineBtn).join('')}</div>
          </div>
          <div class="bg-slate-900/40 p-2.5 rounded-xl border border-slate-800">
             <p class="text-slate-200 text-xs font-bold mb-2">Chicken</p>
             <div class="grid grid-cols-3 gap-1.5">${chicken.map(inlineBtn).join('')}</div>
          </div>
       </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-5">
        <div>
           <h3 class="text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2 flex items-center gap-1"><span>🍲</span> WETFRY</h3>
           <div class="grid grid-cols-3 gap-2">${wetfry.map(blockBtn).join('')}</div>
        </div>
        <div>
           <h3 class="text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2 flex items-center gap-1"><span>🥬</span> GREENS & KACHUMBARI</h3>
           <div class="grid grid-cols-2 gap-2">${greens.map(blockBtn).join('')}</div>
        </div>
        <div>
           <h3 class="text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2 flex items-center gap-1"><span>🥤</span> DRINKS & WATER</h3>
           <div class="grid grid-cols-2 gap-2">${drinks.map(blockBtn).join('')}</div>
        </div>
        <div>
           <h3 class="text-amber-500 text-[10px] font-black uppercase tracking-widest mb-2 flex items-center gap-1"><span>🍟</span> CHIPS & PACKAGING</h3>
           <div class="grid grid-cols-3 gap-2">${chips.map(blockBtn).join('')}</div>
        </div>
    </div>
  `;
}

function triggerQuantityModal(name, category, price) {
  pendingItem = { name, category, price };
  
  let displayName = name;
  if (category === 'MEAT CUTS') displayName = `${name} (${price}/=)`;
  
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
    displayName = `${name} (${price}/=)`; 
  }

  const existing = cart.find(i => i.item_name === displayName && i.unit_price === price);
  if (existing) {
    existing.quantity += qty;
    existing.subtotal = existing.quantity * existing.unit_price;
  } else {
    cart.push({
      item_name: displayName,
      category: category,
      unit_price: price,
      quantity: qty,
      subtotal: price * qty
    });
  }
  updateState();
}

function renderCart() {
  const container = document.getElementById('cartList');
  if (cart.length === 0) {
    container.innerHTML = `<p class="text-slate-500 text-xs text-center py-10">No items selected yet.</p>`;
    document.getElementById('cartTotal').innerText = '0.00';
    validatePaymentInputs();
    return;
  }

  let grandTotal = 0;
  container.innerHTML = cart.map((item, index) => {
    grandTotal += item.subtotal;
    return `
      <div class="flex justify-between items-center bg-slate-900/80 p-2.5 rounded-lg border border-slate-800 text-xs mb-2">
        <div>
          <p class="font-bold text-slate-200">${item.item_name}</p>
          <p class="text-[10px] text-slate-400">${item.quantity} x KSh ${item.unit_price}</p>
        </div>
        <div class="flex items-center gap-3">
          <span class="font-bold text-amber-400">KSh ${item.subtotal}</span>
          <button onclick="requestAdminAction('cart_remove', '${index}')" class="text-red-400 hover:text-red-300 font-bold px-1.5 py-0.5 bg-red-500/10 rounded">✕</button>
        </div>
      </div>
    `;
  }).join('');

  document.getElementById('cartTotal').innerText = grandTotal.toFixed(2);
  validatePaymentInputs();
}

function selectPaymentMethod(method) {
  document.getElementById('paymentMethod').value = method;
  const partialFields = document.getElementById('partialFields');
  
  if (method === 'partial') {
    partialFields.classList.remove('hidden');
  } else {
    partialFields.classList.add('hidden');
  }
  
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
      errorMsg.innerText = `Amounts do not tally! Cash + M-Pesa (${tally}) must equal Total (${total}).`;
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

async function submitOrder() {
  const total = parseFloat(document.getElementById('cartTotal').innerText);
  const method = document.getElementById('paymentMethod').value;
  const user = JSON.parse(localStorage.getItem('sg_user') || '{}');

  const payload = {
    cashier_id: user.id || "00000000-0000-0000-0000-000000000000",
    payment_method: method,
    cash_amount: method === 'partial' ? parseFloat(document.getElementById('cashInput').value) : (method === 'cash' ? total : 0),
    mpesa_amount: method === 'partial' ? parseFloat(document.getElementById('mpesaInput').value) : (method === 'mpesa' ? total : 0),
    total_amount: total,
    items: cart
  };

  try {
    const res = await fetch(`${API_POS}/checkout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getAuthToken()}`
      },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      alert("Order Processed Successfully!");
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
    container.innerHTML = `<p class="text-slate-500 text-xs text-center py-6">No held orders.</p>`;
    return;
  }

  container.innerHTML = holdQueue.map((order, index) => `
    <div class="flex justify-between items-center bg-slate-900 p-2.5 rounded-lg border border-slate-800 text-xs">
      <div>
        <p class="font-bold text-amber-400">Hold #${index + 1} <span class="text-slate-500 font-normal ml-2">${order.time}</span></p>
        <p class="text-slate-300 mt-1">KSh ${order.total} (${order.items.length} items)</p>
      </div>
      <div class="flex gap-2">
        <button onclick="resumeHold(${index})" class="text-amber-400 bg-amber-500/10 px-2 py-1.5 rounded font-bold hover:bg-amber-500/20">Resume</button>
        <button onclick="requestAdminAction('hold', '${order.id}')" class="text-red-400 bg-red-500/10 px-2 py-1.5 rounded font-bold hover:bg-red-500/20">Drop</button>
      </div>
    </div>
  `).join('');
}

function resumeHold(index) {
  if(cart.length > 0) {
    alert("Please clear or hold the current cart first.");
    return;
  }
  cart = holdQueue[index].items;
  holdQueue.splice(index, 1);
  updateState();
}

async function submitExpense(e) {
  e.preventDefault();
  const desc = document.getElementById('expDesc').value.trim();
  const amt = parseFloat(document.getElementById('expAmt').value);
  const type = document.getElementById('expType').value;

  if (amt > 1000) {
    alert("Error: Single expenses cannot exceed 1000 KSh.");
    return;
  }
  
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
      loadReceipts(); 
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
    
    let html = `<h4 class="text-xs font-bold text-slate-400 mb-2 border-b border-slate-800 pb-1">SALES (${data.transactions.length})</h4>`;
    
    if (data.transactions.length === 0) html += `<p class="text-slate-600 text-xs mb-4">No sales recorded yet.</p>`;
    
    html += data.transactions.map(t => `
      <div class="bg-slate-900 p-2.5 rounded-lg border border-slate-800 mb-2 text-xs flex justify-between items-center">
        <div>
          <span class="font-bold text-slate-200">Sale #${t.id.split('-')[0]}</span>
          <p class="text-slate-400 mt-0.5">KSh ${t.total_amount} <span class="uppercase bg-slate-800 px-1 py-0.5 rounded text-[9px] ml-1">${t.payment_method}</span></p>
        </div>
        <button onclick="requestAdminAction('sale', '${t.id}')" class="text-red-400 bg-red-500/10 px-2 py-1 rounded font-bold hover:bg-red-500/20">Delete</button>
      </div>
    `).join('');
    
    html += `<h4 class="text-xs font-bold text-slate-400 mt-6 mb-2 border-b border-slate-800 pb-1">EXPENSES (${data.expenses.length})</h4>`;
    
    if (data.expenses.length === 0) html += `<p class="text-slate-600 text-xs">No expenses recorded yet.</p>`;
    
    html += data.expenses.map(ex => `
      <div class="bg-slate-900 p-2.5 rounded-lg border border-slate-800 mb-2 text-xs flex justify-between items-center">
        <div>
          <span class="font-bold text-slate-200">${ex.description}</span>
          <p class="text-red-400 mt-0.5 font-bold">- KSh ${ex.amount} <span class="uppercase bg-slate-800 px-1 py-0.5 rounded text-[9px] ml-1 text-slate-400">${ex.payment_type}</span></p>
        </div>
        <button onclick="requestAdminAction('expense_delete', '${ex.id}')" class="text-slate-400 bg-slate-800 px-2 py-1 rounded font-bold hover:text-red-400">Delete</button>
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
    if(res.ok) {
      loadReceipts();
    } else {
      alert("Failed to delete expense.");
    }
  } catch(e) {
    alert("Network error.");
  }
}

async function requestAdminAction(actionType, targetId) {
  if (actionType === 'cart_clear' && cart.length === 0) return;

  const user = JSON.parse(localStorage.getItem('sg_user') || '{}');
  
  try {
    const res = await fetch(`${API_POS}/request-delete-qr`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getAuthToken()}`
      },
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
    alert("Could not request authorization. Please check network connection.");
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