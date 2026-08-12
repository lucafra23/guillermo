let pollingTimer = null;
let pollIteration = 0;
const monitoredObjectIds = new Set();

/**
 * Resolves the admin base URL. If a model label (app.model) is provided,
 * it tries to construct the specific admin path for that model.
 */
const getAdminBaseUrl = (modelLabel = null) => {
    if (modelLabel && modelLabel.includes('.')) {
        const [app, model] = modelLabel.split('.');
        // Returns /admin/app/model
        return `${window.location.origin}/admin/${app}/${model}`;
    }
    // Default to the current page's admin path (e.g., /admin/scene/scene)
    return window.location.pathname.split('/').slice(0, 4).join('/');
};

/**
 * Replaces innerHTML of an element and forces the browser to evaluate and 
 * execute any <script> tags contained inside the HTML.
 */
window.setHTMLWithScripts = (element, html) => {
    element.innerHTML = html;

    // requestAnimationFrame guarantees the DOM is fully laid out and painted
    // before we initialize complex visual components like EasyMDE.
    requestAnimationFrame(() => {
        const scripts = element.querySelectorAll('script');
        scripts.forEach(oldScript => {
            const newScript = document.createElement('script');
            Array.from(oldScript.attributes).forEach(attr => newScript.setAttribute(attr.name, attr.value));
            
            let scriptContent = oldScript.innerHTML;
            
            // Bypass DOMContentLoaded / load wrappers so scripts execute instantly on AJAX load
            if (scriptContent.includes("DOMContentLoaded") || scriptContent.includes("window.addEventListener")) {
                console.log("[AdminAjax] Bypassing lifecycle event wrappers for dynamic widget script.");
                scriptContent = scriptContent
                    .replace(/document\.addEventListener\s*\(\s*['"]DOMContentLoaded['"]\s*,\s*(?:function\s*\(\s*\)\s*\{|.*\s*=>\s*\{)/, '')
                    .replace(/window\.addEventListener\s*\(\s*['"]load['"]\s*,\s*(?:function\s*\(\s*\)\s*\{|.*\s*=>\s*\{)/, '');
            }

            newScript.appendChild(document.createTextNode(scriptContent));
            oldScript.parentNode.replaceChild(newScript, oldScript);
        });

        if (window.Alpine) {
            console.log("[AdminAjax] Initializing Alpine.js tree for element:", element);
            window.Alpine.initTree(element);
        }

        if (window.initializeMarkdownEditors) {
            console.log("[AdminAjax] Initializing dynamic Markdown editors for element:", element);
            window.initializeMarkdownEditors(element);
        }
    });
};

/**
 * Safely syncs EasyMDE rich-text content back to the original textarea value.
 */
const syncEasyMDE = (textarea) => {
    if (!textarea || !textarea.easymde) return;
    try {
        if (typeof textarea.easymde.save === 'function') {
            textarea.easymde.save();
        } else if (textarea.easymde.codemirror && typeof textarea.easymde.codemirror.save === 'function') {
            textarea.easymde.codemirror.save();
        } else if (typeof textarea.easymde.value === 'function') {
            textarea.value = textarea.easymde.value();
        }
    } catch (err) {
        console.error("[AdminAjax] Failed to sync EasyMDE value:", err);
    }
};

const setChatFormState = (objectId, disabled) => {
    const formContainer = document.querySelector(`.chat-form[data-object-id="${objectId}"]`);
    if (!formContainer) return;

    const input = formContainer.querySelector('textarea[name="chat_input"]');
    const actionSelect = formContainer.querySelector('select[name="action"]');
    const submitBtn = formContainer.querySelector('.chat-submit-btn');

    if (input) input.disabled = disabled;
    if (actionSelect) actionSelect.disabled = disabled;
    if (submitBtn) submitBtn.disabled = disabled;
    formContainer.style.opacity = disabled ? '0.7' : '1';
};

const updateRowState = (row, status) => {
        if (!row) return;
        // Define which statuses lock the row
        const isLocked = ["0", "1", "5", "6"].includes(status); // 0: Started, 1: Pending, 5: Scheduled, 6: Retry
        // Find all inputs in the row except the dropdown itself
        const inputs = row.querySelectorAll('input, textarea, select:not(.inline-block select)');
        const saveBtn = row.querySelector('.ajax-save-btn');

        inputs.forEach(input => {
            input.disabled = isLocked;
            input.style.opacity = isLocked ? '0.5' : '1';
        });
        
        if (saveBtn) {
            saveBtn.disabled = isLocked;
            saveBtn.style.pointerEvents = isLocked ? 'none' : 'auto';
        }
    };

const startPolling = () => {
    console.log("[AdminAjax] startPolling called. monitoredObjectIds size:", monitoredObjectIds.size);
    pollIteration = 0; // Reset counter whenever a new task is added to trigger fast polling
    if (!pollingTimer) {
        console.log("[AdminAjax] Starting new polling timer.");
        pollingTimer = setTimeout(pollStatus, 1000);
    }
};

const pollStatus = () => {
    console.log("[AdminAjax] pollStatus execution triggered. Iteration:", pollIteration);
    if (monitoredObjectIds.size === 0) {
        console.log("[AdminAjax] No more objects to monitor. Clearing timer.");
        pollingTimer = null;
        return;
    }

        console.log(`[AdminAjax] Active Monitoring Set: ${Array.from(monitoredObjectIds).join(', ')}`);
        monitoredObjectIds.forEach(objectId => {
            const el = document.getElementById(`task-${objectId}`);
            if (!el) {
                console.warn(`[AdminAjax] Container #task-${objectId} not found in DOM. Removing from queue.`);
                monitoredObjectIds.delete(objectId);
                return;
            }
            
            const row = el.closest('tr') || el.closest('.gallery-card-wrapper');
            const modelLabel = el.getAttribute('data-model');
            const url = `${getAdminBaseUrl(modelLabel)}/ajax-last-tasks/${objectId}/`;
        
            fetch(url)
                .then(response => response.json())
                .then(data => {
                    console.log(`[AdminAjax] Received update for ID ${objectId}:`, data);
                    
                    // 1. Update the HTML of the dropdown container
                    el.outerHTML = data.html;

                    // Re-fetch the element to ensure it still has data-status for subsequent loops
                    const updatedEl = document.getElementById(`task-${objectId}`);
                    if (updatedEl) {
                        updatedEl.setAttribute('data-status', String(data.status));
                    }

                    // Remove from monitoring if terminal status reached (specifically 4 as requested)
                    // We also check for any status that isn't pending (0 or 1) to avoid infinite polling on errors
                    if (data.status == "4" || (data.status != "0" && data.status != "1")) {
                        console.log(`[AdminAjax] Terminal status [${data.status}] reached for ID ${objectId}. Stopping polling for this item.`);
                        monitoredObjectIds.delete(objectId);
                    }
                    
                    // 2. If task succeeded, update row content with serialized data
                    if (row && data.object) {
                        // Update editable inputs in the row
                        console.log(`[AdminAjax] Syncing model fields for row ID ${objectId}`);
                        Object.keys(data.object).forEach(key => {
                            const input = row.querySelector(`[name$="-${key}"]`);
                            if (input && input.value !== String(data.object[key])) {
                                input.value = data.object[key];
                            }
                        });
                    }

                    applyRefreshData(row, data.refresh, objectId);

                    
                    // 3. Toggle row inputs based on new status
                    if (row) {
                        updateRowState(row, String(data.status));
                    }
                    setChatFormState(objectId, ["0", "1"].includes(String(data.status)));
                })
                .catch(err => {
                    console.error(`[AdminAjax] Fetch error for object ID ${objectId} at ${url}:`, err);
                });
        });

        // Calculate next delay: 1s for the first 3 polls, then 5s
        const delay = pollIteration < 3 ? 1000 : 5000;
        console.log(`[AdminAjax] Next poll scheduled in ${delay}ms`);
        pollIteration++;
        pollingTimer = setTimeout(pollStatus, delay);
    };

const logShiftFields = () => {
    const list = window.ajaxShiftFields || [];
    console.log(`%c[AdminAjax] Configured Shift+Enter fields: ${JSON.stringify(list)}`, "color: #3b82f6; font-weight: bold;");
};

document.addEventListener('keydown', function(e) {
    if (e.target.name && e.target.name.startsWith('form-')) {
        const input = e.target;
        const cleanName = input.name.split('-').slice(-1)[0];
        const shiftFields = window.ajaxShiftFields || [];
        const isShiftField = shiftFields.includes(cleanName);

        // Debug log to verify field detection
        console.log(`[AdminAjax] Keydown on: ${cleanName} | isShiftField: ${isShiftField} | Key: ${e.key} | Shift: ${e.shiftKey}`);
        
        const shouldTrigger = isShiftField ? (e.key === 'Enter' && e.shiftKey) : (e.key === 'Enter' && !e.shiftKey);

        if (shouldTrigger) {
            e.preventDefault();
            const row = input.closest('tr');
            const idInput = row ? row.querySelector('input.action-select') : null;
            if (!idInput) return;

            const objectId = idInput.value;
            const targetField = input.name.split('-').slice(-1)[0];
            const formData = new URLSearchParams();

            row.querySelectorAll('input, textarea, select').forEach(el => {
                if (el.name && !el.classList.contains('action-select')) {
                    // Sync EasyMDE content if applicable before reading value
                    if (el.tagName && el.tagName.toLowerCase() === 'textarea' && el.easymde) {
                        console.log("[AdminAjax] Syncing EasyMDE on Shift+Enter save for:", el.name);
                        syncEasyMDE(el);
                    }
                    const fieldName = el.name.split('-').slice(-1)[0];
                    const val = el.type === 'checkbox' ? (el.checked ? 'on' : '') : el.value;
                    formData.set(fieldName, val);
                }
            });
            formData.append('target_field', targetField);

            fetch(`${getAdminBaseUrl()}/ajax-update/${objectId}/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: formData
            })
            .then(response => {
                return response.json();
            })
            .then(data => {
                console.log(data)
                if (data.status === 1) {
                    // A task was created, so now we lock the row.
                    if (row) {
                        updateRowState(row, "1"); // Use status 1 (Pending)
                    }


                    // Immediate UI update
                    applyRefreshData(row, data.refresh, objectId);
                    
                    // Update task column HTML so the dropdown appears and polling can start
                    const taskCell = row ? row.querySelector('.field-last_tasks') : document.querySelector(`.field-last_tasks[data-id="${objectId}"]`);
                    if (taskCell && data.html) {
                        taskCell.innerHTML = data.html;
                    }
                    
                    const dropdowns = document.getElementById(`task-${objectId}`);
                    if (dropdowns) {
                        dropdowns.setAttribute('data-status', '1');
                        monitoredObjectIds.add(objectId);
                        startPolling();
                    }
                } else {
                    // No task was created, so ensure the row is not locked.
                    if (row) {
                        updateRowState(row, null); // Pass null to unlock
                    }
                    
                }
            });
        }
    }
});

const applyRefreshData = (container, refreshData, objectId) => {
    if (!refreshData) return;
    
    Object.keys(refreshData).forEach(key => {
        // Try to find the field scoped to this specific object ID
        const selector = `.field-${key}[data-id="${objectId}"], [data-id="${objectId}"] .field-${key}, .field-${key}`;
        const fieldEl = container ? container.querySelector(`.field-${key}`) : document.querySelector(selector);
        
        if (fieldEl) {
            console.log(`[AdminAjax] Refreshing field '${key}' content`);
            fieldEl.innerHTML = refreshData[key];
        }
    });
    addInputHints();
};

// Handle Prompt Preview Presets
document.addEventListener('change', function(e) {
    if (e.target.classList.contains('prompt-preset-select')) {
        const select = e.target;
        const preset = select.value;
        const url = select.getAttribute('data-url');
        const container = select.closest('.prompt-preview-container');
        const contentArea = container.querySelector('.prompt-preview-content');

        contentArea.textContent = "Loading preview...";

        fetch(`${url}?preset=${preset}`)
            .then(response => response.json())
            .then(data => {
                contentArea.textContent = data.content || "No content generated for this preset.";
            })
            .catch(err => {
                contentArea.textContent = "Error loading prompt preview.";
            });
    }
});

async function copyImageToClipboard(imageUrl, buttonSpan) {
    try {
        buttonSpan.textContent = "Copying...";
        const response = await fetch(imageUrl);
        const blob = await response.blob();
        
        let pngBlob = blob;
        // The System Clipboard API generally expects images in 'image/png' format.
        // If the resource is not a PNG, we project it onto a canvas and export to PNG.
        if (!blob.type.includes('png')) {
            pngBlob = await new Promise((resolve, reject) => {
                const img = new Image();
                img.crossOrigin = "anonymous";
                img.onload = () => {
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    canvas.toBlob((b) => {
                        if (b) resolve(b);
                        else reject(new Error("Canvas toBlob failed"));
                    }, 'image/png');
                };
                img.onerror = () => reject(new Error("Failed to load image for PNG copy"));
                img.src = imageUrl;
            });
        }

        await navigator.clipboard.write([
            new ClipboardItem({ [pngBlob.type]: pngBlob })
        ]);
        buttonSpan.textContent = "Copied Image!";
    } catch (err) {
        console.error("[AdminAjax] Failed to copy image:", err);
        buttonSpan.textContent = "Copy Failed";
    }
}

// Image Dropdown Menu Logic
document.addEventListener('click', function(e) {
    const container = e.target.closest('.image-menu-container');
    const existingMenu = document.querySelector('.image-dropdown-menu');

    // Close existing menu if clicking outside
    if (existingMenu && !existingMenu.contains(e.target)) {
        existingMenu.remove();
    }

    // Allow the menu to trigger on both actual images and our new placeholders
    const isImageTrigger = e.target.tagName === 'IMG' || e.target.closest('.image-placeholder');

    if (!container || !isImageTrigger) {
        document.querySelectorAll('.image-dropdown-menu').forEach(m => m.remove());
        return;
    }

    e.preventDefault();
    e.stopPropagation();

    document.querySelectorAll('.image-dropdown-menu').forEach(m => m.remove());

    const menu = document.createElement('div');
    // Appending to body and using absolute positioning to avoid clipping by table row overflow
    menu.className = 'image-dropdown-menu absolute z-[999] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-md shadow-xl py-1 text-xs min-w-[160px] overflow-hidden';
    
    const rect = e.target.getBoundingClientRect();
    menu.style.top = (rect.top + window.scrollY) + 'px';
    menu.style.left = (rect.left + window.scrollX) + 'px';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 transition-colors';
    copyBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">content_copy</span> <span>Copy URL</span>';
    copyBtn.onclick = (ev) => {
        ev.stopPropagation();
        navigator.clipboard.writeText(container.dataset.url).then(() => {
            const span = copyBtn.querySelector('span:last-child');
            span.textContent = "Copied!";
            setTimeout(() => menu.remove(), 800);
        });
    };

    const pasteBtn = document.createElement('button');
    pasteBtn.className = 'w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 transition-colors';
    pasteBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">content_paste</span> <span>Paste URL</span>';
    pasteBtn.onclick = (ev) => {
        ev.stopPropagation();

        const handleUrl = (url) => {
            const trimmed = url ? url.trim() : "";
            if (trimmed && (trimmed.match(/^https?:\/\/.+/i) || trimmed.startsWith('/media/'))) {
                updateImageField(container, trimmed);
            } else if (url) {
                console.warn("[AdminAjax] Invalid URL format - must start with http/https or /media/");
                alert("Invalid URL format. It must start with http://, https:// or /media/");
            }
            menu.remove();
        };

        const showPromptFallback = (defaultVal = "") => {
            const newUrl = prompt("Enter Image URL to replace this item:", defaultVal);
            console.log("[AdminAjax] URL entered via prompt:", newUrl);
            handleUrl(newUrl);
        };

        if (navigator.clipboard && navigator.clipboard.readText) {
            navigator.clipboard.readText().then(clipText => {
                const text = clipText ? clipText.trim() : "";
                // If it looks like a valid external URL, auto-paste it.
                // If it contains /media/ or isn't a URL, trigger the manual prompt.
                if (text.match(/^https?:\/\/.+/i) && !text.includes('/media/')) {
                    console.log("[AdminAjax] Auto-pasting URL from clipboard:", text);
                    handleUrl(text);
                } else {
                    showPromptFallback(text.includes('/media/') ? '' : text);
                }
            }).catch(() => showPromptFallback());
        } else {
            showPromptFallback();
        }
    };

    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 transition-colors';
    downloadBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">download</span> <span>Download Original</span>';
    downloadBtn.onclick = (ev) => {
        ev.stopPropagation();
        const imageUrl = container.dataset.url;
        if (imageUrl) {
            const link = document.createElement('a');
            link.href = imageUrl;
            link.download = imageUrl.substring(imageUrl.lastIndexOf('/') + 1); // Suggest filename
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
        menu.remove();
    };

    const zoomBtn = document.createElement('button');
    zoomBtn.className = 'w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 transition-colors';
    zoomBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">zoom_in</span> <span>Zoom</span>';
    zoomBtn.onclick = (ev) => {
        ev.stopPropagation();
        const imageUrl = container.dataset.url;
        if (imageUrl) {
            openZoomPopup(imageUrl);
        }
        menu.remove();
    };

    const copyImageBtn = document.createElement('button');
    copyImageBtn.className = 'w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 transition-colors';
    copyImageBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">image</span> <span>Copy Image</span>';
    copyImageBtn.onclick = (ev) => {
        ev.stopPropagation();
        const imageUrl = container.dataset.url;
        if (imageUrl) {
            const span = copyImageBtn.querySelector('span:last-child');
            copyImageToClipboard(imageUrl, span).then(() => {
                setTimeout(() => menu.remove(), 1000);
            });
        } else {
            menu.remove();
        }
    };

    menu.appendChild(copyBtn);
    menu.appendChild(pasteBtn);
    menu.appendChild(downloadBtn);
    menu.appendChild(zoomBtn);
    menu.appendChild(copyImageBtn);
    document.body.appendChild(menu);
});

// New function for zoom popup
function openZoomPopup(imageUrl) {
    const overlay = document.createElement('div');
    overlay.className = 'image-zoom-overlay';

    const content = document.createElement('div');
    content.className = 'image-zoom-content';

    const img = document.createElement('img');
    img.src = imageUrl;
    img.alt = 'Zoomed Image';
    img.className = 'max-w-full max-h-full object-contain'; // Responsive image

    const closeBtn = document.createElement('button');
    closeBtn.className = 'image-zoom-close-btn';
    closeBtn.innerHTML = '<span class="material-symbols-outlined text-3xl">close</span>';
    closeBtn.onclick = () => {
        document.body.removeChild(overlay);
    };

    content.appendChild(img);
    content.appendChild(closeBtn);
    overlay.appendChild(content);
    document.body.appendChild(overlay);

    const handleEscape = (e) => {
        if (e.key === 'Escape') {
            document.body.removeChild(overlay);
            document.removeEventListener('keydown', handleEscape);
        }
    };
    document.addEventListener('keydown', handleEscape);

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            document.body.removeChild(overlay);
            document.removeEventListener('keydown', handleEscape);
        }
    });
}

function updateImageField(container, url) {
    console.log("[AdminAjax] updateImageField triggered:", {
        model: container.dataset.model,
        id: container.dataset.id,
        field: container.dataset.field,
        url: url
    });

    const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
    if (!csrfInput) {
        console.error("[AdminAjax] CSRF token not found in page.");
        alert("Error: CSRF token missing.");
        return;
    }

    const formData = new FormData();
    formData.append('_model_label', container.dataset.model);
    formData.append('_id', container.dataset.id);
    formData.append('_field', container.dataset.field);
    formData.append('_value', url);

    const endpoint = getAdminBaseUrl() + '/ajax-section-update/';
    console.log("[AdminAjax] Target endpoint:", endpoint);
    
    fetch(endpoint, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrfInput.value,
        },
        body: formData
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            console.log("[AdminAjax] Image update successful");
            const row = container.closest('tr');
            if (row && data.refresh) {
                applyRefreshData(row, data.refresh);
            } else if (data.refresh && data.refresh[container.dataset.field]) {
                container.outerHTML = data.refresh[container.dataset.field];
            } else {
                location.reload(); 
            }
        } else {
            alert("Error: " + (data.error || "Failed to update image"));
            console.log(data.error)
        }
    })
    .catch(err => alert("Communication error: " + err));
}

const addInputHints = () => {
    const shiftFields = window.ajaxShiftFields || [];
    document.querySelectorAll('textarea[name^="form-"], input[name^="form-"][type="text"]').forEach(input => {
        const fieldName = input.name.split('-').pop();
        if (input.nextElementSibling?.classList.contains('ajax-help-text')) return;

        const isShift = shiftFields.includes(fieldName);
        const hint = document.createElement('div');
        hint.className = 'ajax-help-text';
        hint.textContent = isShift ? "⚡ Shift + Enter to save" : "⏎ Enter to save";
        input.after(hint);
    });
};

// Fetch configuration from the server
const fetchConfig = () => {
    return fetch(`${getAdminBaseUrl()}/ajax-config/`)
        .then(response => {
            if (!response.ok) throw new Error("Config fetch failed");
            return response.json();
        })
        .then(data => {
            window.ajaxShiftFields = data.ajax_shift_fields || [];
            logShiftFields();
            addInputHints();
        })
        .catch(err => console.error("[AdminAjax] Could not load config:", err));
};

const scrollToIdFromHash = () => {
    const hash = window.location.hash;
     console.log(`[AdminAjax] Detected hash for scrolling: ${hash}`);
    if (!hash.startsWith('#id=')) return;
    console.log(`[AdminAjax] Detected hash for scrolling: ${hash}`);
    const targetId = hash.substring(4);
    if (!targetId) return;

    // The selector for the checkbox in each row which holds the object ID
    const selector = `input.action-select[value="${targetId}"]`;
    const input = document.querySelector(selector);

    if (input) {
        // The row is the `<tr>` with class `data-row`
        const row = input.closest('tr.data-row');
        if (row) {
            console.log(`[AdminAjax] Scrolling to row with ID: ${targetId}`);
            
            // Scroll the row into the center of the viewport
            row.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Add a temporary highlight effect
            row.style.transition = 'background-color 0.5s ease-in-out';
            const highlightColor = document.documentElement.classList.contains('dark') 
                ? 'rgba(var(--color-primary-500-rgb), 0.2)' 
                : 'rgba(var(--color-primary-300-rgb), 0.4)';
            
            row.style.backgroundColor = highlightColor;

            // Remove the highlight after a couple of seconds
            setTimeout(() => {
                row.style.backgroundColor = '';
            }, 2500);
        }
    }
     console.log(`[AdminAjax] Finished hash for scrolling: ${hash} ${input}`);
};

// Initialize monitoring for any tasks already in progress on page load
const initTaskMonitoring = () => {
    document.querySelectorAll('[id^="task-"]').forEach(el => {
        const row = el.closest('tr');
        if (!row) return; // Only monitor tasks within changelist table rows

        const status = el.getAttribute('data-status');
        if (["0", "1"].includes(status)) {
            const objectId = el.id.replace('task-', '');
            monitoredObjectIds.add(objectId);
            setChatFormState(objectId, true); // Disable chat form if task is active
            updateRowState(row, status);
        }
    });
    if (monitoredObjectIds.size > 0) {
        startPolling();
    }
    fetchConfig();
    scrollToIdFromHash();
};

document.addEventListener('click', function(e) {
    // This function now handles both chat submissions and section refreshes.
    // It's designed to be extensible for other AJAX-triggered section updates.

    // --- Chat Submission Logic ---
    const submitBtn = e.target.closest('.chat-submit-btn');
    if (submitBtn) {
        console.log("[AdminAjax] Chat submit button clicked:", submitBtn);
        e.preventDefault();

        const formContainer = submitBtn.closest('.chat-form');
        if (formContainer) {
            const objectId = formContainer.dataset.objectId;
            const input = formContainer.querySelector('textarea[name="chat_input"]');
            const actionSelect = formContainer.querySelector('select[name="action"]');
            const messagesContainer = document.getElementById(`chat-messages-${objectId}`);
            const url = `${getAdminBaseUrl()}/${formContainer.dataset.url}`;
            const token = formContainer.querySelector('[name=csrfmiddlewaretoken]').value;
            console.log(`[AdminAjax] Preparing to send chat input for object ID: ${objectId} | URL: ${url} | Input: ${input.value} | Token: ${token}`);
            
            // Manually construct FormData since we are not in a <form> element
            const formData = new FormData();
            formData.append('chat_input', input.value);
            if (actionSelect) {
                formData.append('action', actionSelect.value);
            }
            const originalInputValue = input.value;

            // Disable form and show loading state
            setChatFormState(objectId, true);
            
            // Append a temporary user message for immediate feedback
            const tempUserMessage = `
                <div class="flex items-start gap-3 justify-end">
                    <div class="bg-primary-100 dark:bg-primary-900/50 text-primary-800 dark:text-primary-200 rounded-lg p-3 max-w-lg opacity-60">
                        <p class="text-sm">${originalInputValue}</p>
                    </div>
                </div>`;
            messagesContainer.insertAdjacentHTML('afterbegin', tempUserMessage);
            messagesContainer.scrollTop = 0;

            fetch(url, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-CSRFToken': token
                }
            })
            .then(response => response.json())
            .then(data => {
                // Update the chat content with the server-rendered HTML
                messagesContainer.innerHTML = data.html;
                messagesContainer.scrollTop = 0;
                input.value = ''; // Clear input on success

                // If a task was created, start polling for the main object
                if (data.status === 'task_created' && ["0", "1"].includes(String(data.task_status))) {
                    console.log(`[AdminAjax] Task created from chat for object ID ${objectId}. Starting polling.`);
                    monitoredObjectIds.add(objectId);
                    // The form is already disabled by setChatFormState, now we just need to start polling.
                    startPolling();
                }
            })
            .finally(() => {
                // Re-enable the form only if no task is actively running
                if (!monitoredObjectIds.has(objectId)) {
                    setChatFormState(objectId, false);
                }
                input.focus();
            });
        }
        return;
    }

    // --- Generic Form Submission Logic ---
    const formSubmitBtn = e.target.closest('.generic-form-submit-btn');
    if (formSubmitBtn) {
        e.preventDefault();
        console.log("[AdminAjax] >>> Generic form submit button clicked:", formSubmitBtn);
        
        const section = formSubmitBtn.closest('.generic-form-section-container');
        if (!section) {
            console.error("[AdminAjax] Failure: Parent container '.generic-form-section-container' not found for button:", formSubmitBtn);
            return;
        }
        console.log("[AdminAjax] Found parent section:", section);

        const formContainer = section.querySelector('.generic-form-body');
        if (!formContainer) {
            console.error("[AdminAjax] Failure: '.generic-form-body' not found inside section:", section);
            return;
        }
        console.log("[AdminAjax] Found form container body:", formContainer);

        // Force all EasyMDE markdown editors within the form container to sync their content
        formContainer.querySelectorAll('textarea').forEach(textarea => {
            if (textarea.easymde) {
                console.log("[AdminAjax] Syncing EasyMDE editor content back to textarea:", textarea.name);
                syncEasyMDE(textarea);
            }
        });

        let url = formContainer.getAttribute('data-action');
        if (url && !url.startsWith('/') && !url.startsWith('http')) {
            url = `${getAdminBaseUrl()}/${url}`;
        }
        console.log("[AdminAjax] Target submit URL resolved to:", url);

        const formData = new FormData();
        formContainer.querySelectorAll('input, textarea, select').forEach(input => {
            if (input.name) {
                if (input.type === 'checkbox' || input.type === 'radio') {
                    if (input.checked) {
                        formData.append(input.name, input.value);
                    }
                } else if (input.type === 'file') {
                    if (input.files.length > 0) {
                        formData.append(input.name, input.files[0]);
                    }
                } else {
                    formData.append(input.name, input.value);
                }
            }
        });

        // Print values being submitted for debugging
        console.log("[AdminAjax] Assembled FormData pairs:");
        for (let pair of formData.entries()) {
            console.log(`  - ${pair[0]}:`, pair[1]);
        }

        const tokenEl = formContainer.querySelector('[name=csrfmiddlewaretoken]') || document.querySelector('[name=csrfmiddlewaretoken]');
        if (!tokenEl) {
            console.error("[AdminAjax] Failure: CSRF token input '[name=csrfmiddlewaretoken]' not found anywhere in the form container or document!");
            alert("Error: CSRF token missing.");
            return;
        }
        const token = tokenEl.value;
        console.log("[AdminAjax] CSRF token successfully retrieved:", token.substring(0, 8) + "...");

        // Visual feedback
        formSubmitBtn.disabled = true;
        formSubmitBtn.innerText = "Saving...";
        section.style.opacity = '0.7';

        console.log("[AdminAjax] Dispatching fetch POST request to:", url);
        fetch(url, {
            method: 'POST',
            body: formData,
            headers: {
                'X-CSRFToken': token
            }
        })
        .then(response => {
            console.log("[AdminAjax] Received response with status:", response.status);
            if (!response.ok && response.status !== 400) {
                throw new Error("HTTP error " + response.status);
            }
            return response.json();
        })
        .then(data => {
            console.log("[AdminAjax] Decoded server JSON response data:", data);
            if (data.status === 'success' || data.status === 'error') {
                console.log("[AdminAjax] Replacing inner content with updated HTML.");
                const contentArea = section.querySelector('[x-ref="content"]');
                if (contentArea) {
                    window.setHTMLWithScripts(contentArea, data.html);
                } else {
                    section.outerHTML = data.html;
                }
            }
        })
        .catch(err => {
            console.error("[AdminAjax] Generic form submit error:", err);
            alert("An error occurred while saving.");
        })
        .finally(() => {
            console.log("[AdminAjax] Submission process completed.");
            formSubmitBtn.disabled = false;
            formSubmitBtn.innerText = "Save Details";
            section.style.opacity = '1';
        });
        return;
    }

    // --- Generic Section Refresh Logic ---
    const refreshBtn = e.target.closest('.generic-section-refresh-btn');
    if (refreshBtn) {
        e.preventDefault();
        // Alpine.js handles the click action on refreshBtn via @click="refreshSection()"
        return;
    }
});

// Handle "Read more" / "Read less" in chat messages without AlpineJS
document.addEventListener('click', function(e) {
    const toggleBtn = e.target.closest('.toggle-text-button');
    if (!toggleBtn) return;

    e.preventDefault();
    e.stopPropagation(); // Crucial: Prevent click from bubbling up to the row

    const container = toggleBtn.closest('.expandable-text');
    if (!container) return;

    const summary = container.querySelector('.summary-text');
    const full = container.querySelector('.full-text');

    if (summary && full) {
        summary.style.display = summary.style.display === 'none' ? '' : 'none';
        full.style.display = full.style.display === 'none' ? '' : 'none';
    }
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTaskMonitoring);
} else {
    initTaskMonitoring();
}
