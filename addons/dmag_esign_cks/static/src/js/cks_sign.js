/* Trang ký số của khách: ký bằng USB token (qua DMAG Ký số) hoặc app ký từ xa. */
(function () {
    'use strict';

    var config = JSON.parse(document.getElementById('cks-config').textContent);
    var base = '/cks/ky/' + config.envelopeId;
    var summary = config.summary;
    var pollTimer = null;
    var busy = false;

    var $ = function (id) { return document.getElementById(id); };

    function esc(text) {
        var div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    // ------------------------------------------------------------- gọi Odoo
    function rpc(path, params) {
        params = Object.assign({access_token: config.token}, params || {});
        return fetch(base + path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: params}),
        }).then(function (resp) { return resp.json(); }).then(function (data) {
            if (data.error) {
                var msg = (data.error.data && data.error.data.message) || data.error.message || 'Lỗi không xác định';
                throw new Error(msg);
            }
            return data.result;
        });
    }

    // ------------------------------------------------------------- gọi DMAG Ký số
    function agent(path, body) {
        var opts = body ? {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
        } : {method: 'GET'};
        return fetch(config.agentUrl + path, opts).then(function (resp) {
            return resp.json().then(function (data) {
                if (!resp.ok) {
                    var err = new Error(data.message || 'Phần mềm ký số báo lỗi.');
                    err.code = data.error;
                    throw err;
                }
                return data;
            });
        });
    }

    // ------------------------------------------------------------- hiển thị
    var STATE_ICON = {signed: '✔', waiting: '⏳', pending: '○', rejected: '✖', error: '!'};

    function renderStatus() {
        var html = '<h2>Trạng thái: <span class="cks-pill cks-' + esc(summary.state) + '">' +
            esc(summary.state_label) + '</span></h2><ul class="cks-signers">';
        summary.signers.forEach(function (s) {
            html += '<li class="cks-s-' + esc(s.state) + '"><span class="cks-icon">' + (STATE_ICON[s.state] || '○') +
                '</span><div><strong>' + esc(s.label) + '</strong>' +
                (s.subject ? '<br/><small>' + esc(s.subject) + '</small>' : '') +
                (s.signed_at ? '<br/><small>Ký lúc ' + esc(s.signed_at) + '</small>' : '') + '</div></li>';
        });
        html += '</ul>';
        if (summary.done) {
            html += '<p class="cks-ok">Hợp đồng đã được các bên ký số đầy đủ.</p>' +
                '<a class="cks-btn" href="' + base + '/pdf?download=1&access_token=' +
                encodeURIComponent(config.token) + '">Tải bản đã ký số</a>';
        } else if (summary.waiting_company) {
            html += '<p class="cks-info">Bạn đã ký xong. Hệ thống đang chờ bên phát hành ký số…</p>';
        } else if (summary.state === 'cancel') {
            html += '<p class="cks-error">Hồ sơ ký đã bị hủy.</p>';
        }
        $('cks-status').innerHTML = html;
        var canSign = summary.signer_id && summary.state === 'customer';
        $('cks-sign').hidden = !canSign;
        if (canSign && summary.signer_state === 'waiting') {
            showWaiting(summary.signer_message);
        } else if ($('cks-cloud-wait')) {
            $('cks-cloud-wait').remove();
            $('cks-cloud-start').disabled = false;
            if (canSign && summary.signer_message) {
                showError(summary.signer_message);
            }
        }
        var frame = document.querySelector('.cks-pdf');
        if (summary.done && frame && !frame.dataset.reloaded) {
            frame.dataset.reloaded = '1';
            frame.src = frame.src;
        }
    }

    function showError(msg) {
        var box = $('cks-error');
        box.textContent = msg || '';
        box.hidden = !msg;
    }

    function setBusy(flag) {
        busy = flag;
        document.querySelectorAll('#cks-sign button').forEach(function (b) { b.disabled = flag; });
    }

    function refresh() {
        return rpc('/status').then(function (result) {
            summary = result;
            renderStatus();
            if (summary.done || summary.state === 'cancel' ||
                (summary.state === 'customer' && summary.signer_state !== 'waiting')) {
                stopPolling();
            }
            return result;
        });
    }

    function startPolling() {
        if (!pollTimer) {
            pollTimer = setInterval(function () { refresh().catch(function () {}); }, 3000);
        }
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function afterSigned(result) {
        summary = result;
        renderStatus();
        if (!summary.done) {
            startPolling();
        }
    }

    // ------------------------------------------------------------- USB token
    function renderUsbMissing() {
        $('cks-usb').innerHTML =
            '<p>Để ký bằng USB token, máy tính cần phần mềm <strong>DMAG Ký số</strong> (cài 1 lần, khoảng 1 phút).</p>' +
            '<ol class="cks-steps"><li>Cắm USB token vào máy.</li>' +
            '<li><a class="cks-btn cks-btn-light" href="' + esc(config.agentDownload) + '">Tải DMAG Ký số</a> ' +
            'rồi mở file vừa tải.</li><li>Bấm nút dưới đây.</li></ol>' +
            '<button type="button" class="cks-btn" id="cks-usb-retry">Tôi đã cài xong, tìm chữ ký số</button>' +
            '<p class="cks-hint">Dùng được USB token của mọi nhà cung cấp (VNPT, Viettel, BKAV, FPT, MISA…). ' +
            'Trình duyệt có thể hỏi “cho phép truy cập thiết bị trên máy”, hãy chọn Cho phép.</p>';
        $('cks-usb-retry').onclick = loadCertificates;
    }

    function renderCertificates(certs) {
        if (!certs.length) {
            $('cks-usb').innerHTML = '<p>Không thấy chữ ký số nào trên máy. Hãy cắm USB token, đợi vài giây rồi thử lại.</p>' +
                '<button type="button" class="cks-btn" id="cks-usb-retry">Tìm lại</button>';
            $('cks-usb-retry').onclick = loadCertificates;
            return;
        }
        var html = '<p>Chọn chữ ký số:</p><div class="cks-certs">';
        certs.forEach(function (c, i) {
            var issuer = ((c.issuer || '').match(/CN=([^,]+)/) || [])[1] || c.issuer;
            var expiry = c.not_after ? new Date(c.not_after).toLocaleDateString('vi-VN') : '';
            html += '<label class="cks-cert' + (c.expired ? ' cks-expired' : '') + '">' +
                '<input type="radio" name="cks-cert" value="' + i + '"' + (i === 0 && !c.expired ? ' checked' : '') +
                (c.expired ? ' disabled' : '') + '/>' +
                '<span><strong>' + esc(c.common_name || c.subject) + '</strong><br/>' +
                '<small>' + esc(issuer) + ' · hết hạn ' + esc(expiry) + (c.expired ? ' (đã hết hạn)' : '') +
                '</small></span></label>';
        });
        html += '</div><button type="button" class="cks-btn" id="cks-usb-sign">Ký số</button>' +
            '<p class="cks-hint">Sau khi bấm, máy tính sẽ hỏi xác nhận và mã PIN của USB token.</p>';
        $('cks-usb').innerHTML = html;
        $('cks-usb-sign').onclick = function () {
            var checked = document.querySelector('input[name="cks-cert"]:checked');
            if (!checked) {
                showError('Vui lòng chọn chữ ký số.');
                return;
            }
            signWithUsb(certs[Number(checked.value)]);
        };
    }

    function loadCertificates() {
        showError('');
        $('cks-usb').innerHTML = '<p class="cks-info">Đang tìm phần mềm DMAG Ký số trên máy…</p>';
        agent('/v1/info').catch(function (err) {
            // Phần mềm có chạy nhưng chưa cho phép trang này: gọi tiếp để máy tính hỏi "Cho phép?".
            if (err.code === 'origin_not_allowed') {
                return null;
            }
            throw err;
        }).then(function () {
            return agent('/v1/certificates');
        }).then(function (data) {
            renderCertificates(data.certificates || []);
        }).catch(function (err) {
            if (err.code === 'origin_not_allowed') {
                $('cks-usb').innerHTML = '<p>Phần mềm DMAG Ký số chưa cho phép trang này. Hãy chọn “Có” ở hộp thoại ' +
                    'trên máy tính rồi thử lại.</p><button type="button" class="cks-btn" id="cks-usb-retry">Thử lại</button>';
                $('cks-usb-retry').onclick = loadCertificates;
            } else {
                renderUsbMissing();
            }
        });
    }

    function signWithUsb(cert) {
        if (busy) {
            return;
        }
        setBusy(true);
        showError('');
        rpc('/usb/prepare', {signer_id: summary.signer_id, cert: cert.der, chain: cert.chain || []})
            .then(function (prep) {
                $('cks-usb').innerHTML = '<p class="cks-info">Mời xác nhận và nhập mã PIN USB token trên máy tính…</p>';
                return agent('/v1/sign', {id: cert.id, hash: prep.hash, hash_alg: 'sha256', document: prep.document});
            })
            .then(function (res) {
                $('cks-usb').innerHTML = '<p class="cks-info">Đang hoàn tất chữ ký…</p>';
                return rpc('/usb/finalize', {signer_id: summary.signer_id, signature: res.signature});
            })
            .then(afterSigned)
            .catch(function (err) {
                showError(err.code === 'user_cancelled' ? 'Bạn đã hủy ký.' : err.message);
                loadCertificates();
            })
            .finally(function () { setBusy(false); });
    }

    // ------------------------------------------------------------- ký từ xa
    function showWaiting(message) {
        var pane = document.querySelector('[data-pane="cloud"]');
        selectTab('cloud');
        var note = $('cks-cloud-wait');
        if (!note) {
            note = document.createElement('div');
            note.id = 'cks-cloud-wait';
            note.className = 'cks-wait';
            pane.appendChild(note);
        }
        $('cks-cloud-start').disabled = true;
        note.innerHTML = '<span class="cks-spinner"></span><div><strong>Mời mở ứng dụng ký số trên điện thoại ' +
            'và bấm Xác nhận.</strong><br/><small>' + esc(message || '') + '</small></div>';
        startPolling();
    }

    function cloudStart() {
        if (busy) {
            return;
        }
        setBusy(true);
        showError('');
        rpc('/cloud/start', {
            signer_id: summary.signer_id,
            provider_id: Number($('cks-provider').value),
            remote_user_id: $('cks-remote-user').value,
        }).then(function (result) {
            summary = result;
            renderStatus();
            startPolling();
        }).catch(function (err) {
            showError(err.message);
        }).finally(function () { setBusy(false); });
    }

    // ------------------------------------------------------------- tab
    function selectTab(name) {
        document.querySelectorAll('.cks-tab').forEach(function (tab) {
            tab.classList.toggle('active', tab.dataset.tab === name);
        });
        document.querySelectorAll('.cks-pane').forEach(function (pane) {
            pane.hidden = pane.dataset.pane !== name;
        });
    }

    document.querySelectorAll('.cks-tab').forEach(function (tab) {
        tab.addEventListener('click', function () { selectTab(tab.dataset.tab); });
    });
    if ($('cks-cloud-start')) {
        $('cks-cloud-start').addEventListener('click', cloudStart);
        $('cks-remote-user').value = config.defaultRemoteUser || '';
        var hint = function () {
            var opt = $('cks-provider').selectedOptions[0];
            $('cks-remote-hint').textContent = opt ? opt.dataset.hint || '' : '';
        };
        $('cks-provider').addEventListener('change', hint);
        hint();
    }

    renderStatus();
    if (summary.signer_id && summary.state === 'customer') {
        loadCertificates();
    }
    if (summary.state === 'company' || summary.signer_state === 'waiting') {
        startPolling();
    }
})();
