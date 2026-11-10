(function (Scratch) {
    "use strict";

    if (!Scratch.extensions.unsandboxed) {
        throw new Error("Workspace Editor cần chạy unsandboxed.");
    }

    const SB = window.ScratchBlocks;
    if (!SB) throw new Error("Không tìm thấy ScratchBlocks.");

    const EXT = "workspaceeditor";

    let lastId = "";
    let hoveredId = "";
    let statusText = "Sẵn sàng";

    let panel;
    let toggle;
    let info;
    let statusElement;

    const BLOCK_TYPES = [
        ["Đi 10 bước", "motion_movesteps"],
        ["Xoay phải", "motion_turnright"],
        ["Xoay trái", "motion_turnleft"],
        ["Đi tới tọa độ", "motion_gotoxy"],
        ["Đặt x", "motion_setx"],
        ["Đặt y", "motion_sety"],
        ["Nói", "looks_say"],
        ["Nghĩ", "looks_think"],
        ["Đổi kích thước", "looks_changesizeby"],
        ["Đặt kích thước", "looks_setsizeto"],
        ["Chờ", "control_wait"],
        ["Lặp lại", "control_repeat"],
        ["Mãi mãi", "control_forever"],
        ["Nếu", "control_if"],
        ["Nếu / nếu không", "control_if_else"],
        ["Đợi đến khi", "control_wait_until"],
        ["Dừng", "control_stop"],
        ["Cộng", "operator_add"],
        ["Trừ", "operator_subtract"],
        ["Nhân", "operator_multiply"],
        ["Chia", "operator_divide"],
        ["Số ngẫu nhiên", "operator_random"],
        ["Lớn hơn", "operator_gt"],
        ["Bằng", "operator_equals"],
        ["Nhỏ hơn", "operator_lt"],
        ["Và", "operator_and"],
        ["Hoặc", "operator_or"],
        ["Không", "operator_not"],
        ["Nối chuỗi", "operator_join"],
        ["Độ dài", "operator_length"],
        ["Chứa chuỗi", "operator_contains"],
        ["Khi bấm cờ xanh", "event_whenflagclicked"],
        ["Khi bấm phím", "event_whenkeypressed"],
        ["Khi bấm nhân vật", "event_whenthisspriteclicked"],
        ["Phát tin", "event_broadcast"],
        ["Khi nhận tin", "event_whenbroadcastreceived"],
        ["Chạm cạnh", "sensing_touchingedge"],
        ["Chạm màu", "sensing_touchingcolor"],
        ["Hỏi và đợi", "sensing_askandwait"],
        ["Đặt biến", "data_setvariableto"],
        ["Thay đổi biến", "data_changevariableby"],
        ["Thêm vào danh sách", "data_addtolist"],
        ["Xóa mục danh sách", "data_deleteoflist"],
        ["Lấy mục danh sách", "data_itemoflist"]
    ];

    // Danh sách tên trường/input thường gặp trong Scratch.
    // Mỗi mục có dạng [tên hiển thị, giá trị trả về].
    const FIELD_CHOICES = [
        ["Số bước (STEPS)", "STEPS"],
        ["Góc (DEGREES)", "DEGREES"],
        ["Hướng (DIRECTION)", "DIRECTION"],
        ["Tọa độ X (X)", "X"],
        ["Tọa độ Y (Y)", "Y"],
        ["Giá trị (VALUE)", "VALUE"],
        ["Nội dung nói (MESSAGE)", "MESSAGE"],
        ["Thời gian (SECS)", "SECS"],
        ["Số lần lặp (TIMES)", "TIMES"],
        ["Số (NUM)", "NUM"],
        ["Văn bản (TEXT)", "TEXT"],
        ["Toán hạng 1 (NUM1)", "NUM1"],
        ["Toán hạng 2 (NUM2)", "NUM2"],
        ["Điều kiện (CONDITION)", "CONDITION"],
        ["Nhánh lệnh (SUBSTACK)", "SUBSTACK"],
        ["Nhánh lệnh 2 (SUBSTACK2)", "SUBSTACK2"],
        ["Biến (VARIABLE)", "VARIABLE"],
        ["Danh sách (LIST)", "LIST"],
        ["Chỉ mục (INDEX)", "INDEX"],
        ["Mục (ITEM)", "ITEM"],
        ["Tin nhắn (BROADCAST_OPTION)", "BROADCAST_OPTION"],
        ["Phím (KEY_OPTION)", "KEY_OPTION"],
        ["Tùy chọn dừng (STOP_OPTION)", "STOP_OPTION"],
        ["Màu (COLOR)", "COLOR"],
        ["Kích thước (SIZE)", "SIZE"],
        ["Hiệu ứng (EFFECT)", "EFFECT"],
        ["Giá trị hiệu ứng (EFFECT_VALUE)", "EFFECT_VALUE"],
        ["Trang phục (COSTUME)", "COSTUME"],
        ["Phông nền (BACKDROP)", "BACKDROP"],
        ["Thuộc tính (PROPERTY)", "PROPERTY"]
    ];

    function workspace() {
        return SB.getMainWorkspace?.() || null;
    }

    function getBlock(id) {
        const cleanId = String(id ?? "").trim();
        if (!cleanId) return null;
        return workspace()?.getBlockById(cleanId) || null;
    }

    function setStatus(message) {
        statusText = String(message);
        if (statusElement) statusElement.textContent = statusText;
    }

    function safeLabel(value, max = 65) {
        const text = String(value ?? "");
        return text.length > max
            ? text.slice(0, max - 3) + "..."
            : text;
    }

    function getFields(block) {
        const result = [];

        for (const input of block.inputList || []) {
            for (const field of input.fieldRow || []) {
                if (
                    field &&
                    typeof field.getName === "function" &&
                    field.getName()
                ) {
                    result.push({
                        name: field.getName(),
                        value: field.getValue(),
                        text: field.getText()
                    });
                }
            }
        }

        return result;
    }

    function getInputs(block) {
        return (block.inputList || []).map(input => ({
            name: input.name || "",
            connected: !!input.connection?.targetBlock(),
            childId: input.connection?.targetBlock()?.id || ""
        }));
    }

    function getBlockTypes() {
        return BLOCK_TYPES.filter(([, type]) => !!SB.Blocks?.[type]);
    }

    function getAllFieldsMenu() {
        // Trả về các cặp [label, value] theo định dạng menu Scratch.
        return FIELD_CHOICES.map(item => [item[0], item[1]]);
    }

    function copyText(value) {
        const text = String(value || "");

        if (!text) {
            setStatus("Không có ID để sao chép.");
            return;
        }

        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text)
                .then(() => setStatus("Đã sao chép ID: " + text))
                .catch(() => fallbackCopy(text));
        } else {
            fallbackCopy(text);
        }
    }

    function fallbackCopy(text) {
        const el = document.createElement("textarea");
        el.value = text;

        Object.assign(el.style, {
            position: "fixed",
            left: "-9999px",
            top: "0"
        });

        document.body.appendChild(el);
        el.select();

        let ok = false;
        try {
            ok = document.execCommand("copy");
        } catch (_) {}

        el.remove();

        setStatus(
            ok
                ? "Đã sao chép ID: " + text
                : "Không thể tự sao chép. ID: " + text
        );
    }

    function showBlock(block) {
        if (!block) {
            if (info) info.textContent = "Chưa phát hiện khối.";
            return;
        }

        hoveredId = block.id;

        const fields = getFields(block);
        const inputs = getInputs(block);

        if (!info) return;

        info.textContent =
            `ID: ${block.id}\n` +
            `Type: ${block.type}\n\n` +
            `Trường:\n` +
            (fields.length
                ? fields.map(f =>
                    `  ${f.name} = ${safeLabel(f.text, 30)}`
                ).join("\n")
                : "  (không có)") +
            `\n\nInput:\n` +
            (inputs.length
                ? inputs.map(i =>
                    `  ${i.name || "(không tên)"}: ` +
                    `${i.connected ? "đã nối" : "trống"}`
                ).join("\n")
                : "  (không có)");
    }

    function findBlockFromElement(element) {
        const ws = workspace();
        let node = element;

        while (node && node !== document) {
            if (node.getAttribute) {
                const ids = [
                    node.getAttribute("data-id"),
                    node.id
                ];

                for (const id of ids) {
                    if (!id) continue;
                    const block = ws?.getBlockById(id);
                    if (block) return block;
                }
            }

            node = node.parentElement;
        }

        return null;
    }

    function makeButton(label, action) {
        const button = document.createElement("button");
        button.textContent = label;

        Object.assign(button.style, {
            padding: "7px 9px",
            border: "1px solid #555",
            borderRadius: "6px",
            background: "#333",
            color: "#fff",
            cursor: "pointer",
            textAlign: "left",
            fontSize: "12px"
        });

        button.addEventListener("click", action);
        return button;
    }

    function createPanel() {
        if (document.getElementById("workspace-editor-toggle")) return;

        panel = document.createElement("div");
        panel.id = "workspace-editor-panel";

        Object.assign(panel.style, {
            position: "fixed",
            right: "18px",
            bottom: "68px",
            width: "285px",
            maxHeight: "70vh",
            overflowY: "auto",
            display: "none",
            flexDirection: "column",
            gap: "8px",
            padding: "12px",
            background: "#202124",
            color: "#fff",
            border: "1px solid #555",
            borderRadius: "10px",
            boxShadow: "0 4px 16px #0006",
            zIndex: "999998",
            fontFamily: "Arial, sans-serif",
            fontSize: "12px",
            boxSizing: "border-box"
        });

        const heading = document.createElement("div");
        heading.textContent = "Workspace Editor";

        Object.assign(heading.style, {
            fontSize: "15px",
            fontWeight: "bold"
        });

        panel.appendChild(heading);

        info = document.createElement("pre");
        info.textContent = "Rê chuột lên khối để xem ID.";

        Object.assign(info.style, {
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            background: "#151515",
            borderRadius: "6px",
            padding: "8px",
            margin: "0",
            lineHeight: "1.5",
            fontFamily: "monospace",
            fontSize: "11px",
            userSelect: "text"
        });

        panel.appendChild(info);

        panel.appendChild(makeButton(
            "Sao chép ID khối đang rê chuột",
            () => copyText(hoveredId)
        ));

        panel.appendChild(makeButton(
            "Sao chép ID khối vừa tạo",
            () => copyText(lastId)
        ));

        statusElement = document.createElement("div");
        statusElement.textContent = statusText;

        Object.assign(statusElement.style, {
            color: "#bdbdbd",
            overflowWrap: "anywhere",
            whiteSpace: "pre-wrap"
        });

        panel.appendChild(statusElement);

        panel.appendChild(makeButton("Thu nhỏ bảng", () => {
            panel.style.display = "none";
        }));

        document.body.appendChild(panel);

        toggle = document.createElement("button");
        toggle.id = "workspace-editor-toggle";
        toggle.type = "button";
        toggle.title = "Kéo để di chuyển · Nhấn để mở bảng";
        toggle.setAttribute("aria-label", "Mở Workspace Editor");

        toggle.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24"
                 fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M6 3.8C6 2.8 7 2.2 7.9 2.8L20 11.1
                         C21 11.8 20.6 13.1 19.4 13.4L14.8 14.6
                         L12.4 19.8C11.9 20.9 10.5 20.8 10.1 19.7Z"
                      fill="currentColor" stroke="currentColor"
                      stroke-width="1.2" stroke-linejoin="round"/>
            </svg>`;

        Object.assign(toggle.style, {
            position: "fixed",
            right: "18px",
            bottom: "18px",
            width: "42px",
            height: "42px",
            padding: "0",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid #555",
            borderRadius: "50%",
            background: "#222",
            color: "#fff",
            boxShadow: "0 2px 8px #0005",
            cursor: "grab",
            zIndex: "999999",
            touchAction: "none",
            userSelect: "none"
        });

        document.body.appendChild(toggle);

        let dragging = false;
        let moved = false;
        let startX = 0;
        let startY = 0;
        let startLeft = 0;
        let startTop = 0;
        let ignoreClickUntil = 0;

        toggle.addEventListener("pointerdown", event => {
            if (event.pointerType === "mouse" && event.button !== 0) return;

            dragging = true;
            moved = false;

            const rect = toggle.getBoundingClientRect();

            startX = event.clientX;
            startY = event.clientY;
            startLeft = rect.left;
            startTop = rect.top;

            toggle.style.left = rect.left + "px";
            toggle.style.top = rect.top + "px";
            toggle.style.right = "auto";
            toggle.style.bottom = "auto";
            toggle.style.cursor = "grabbing";

            try {
                toggle.setPointerCapture(event.pointerId);
            } catch (_) {}

            event.preventDefault();
        });

        toggle.addEventListener("pointermove", event => {
            if (!dragging) return;

            const dx = event.clientX - startX;
            const dy = event.clientY - startY;

            if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
            if (!moved) return;

            const maxLeft = Math.max(0, window.innerWidth - toggle.offsetWidth);
            const maxTop = Math.max(0, window.innerHeight - toggle.offsetHeight);

            toggle.style.left =
                Math.max(0, Math.min(maxLeft, startLeft + dx)) + "px";
            toggle.style.top =
                Math.max(0, Math.min(maxTop, startTop + dy)) + "px";
        });

        function finishDrag() {
            if (!dragging) return;

            dragging = false;
            toggle.style.cursor = "grab";

            if (moved) ignoreClickUntil = Date.now() + 250;
        }

        toggle.addEventListener("pointerup", finishDrag);
        toggle.addEventListener("pointercancel", finishDrag);

        toggle.addEventListener("click", () => {
            if (Date.now() < ignoreClickUntil) return;

            panel.style.display =
                panel.style.display === "none" ? "flex" : "none";

            if (panel.style.display !== "none" && hoveredId) {
                showBlock(getBlock(hoveredId));
            }
        });

        window.addEventListener("resize", () => {
            if (!toggle) return;

            const rect = toggle.getBoundingClientRect();

            const left = Math.max(
                0,
                Math.min(window.innerWidth - toggle.offsetWidth, rect.left)
            );

            const top = Math.max(
                0,
                Math.min(window.innerHeight - toggle.offsetHeight, rect.top)
            );

            toggle.style.left = left + "px";
            toggle.style.top = top + "px";
            toggle.style.right = "auto";
            toggle.style.bottom = "auto";
        });

        document.addEventListener("pointermove", event => {
            if (
                panel?.contains(event.target) ||
                toggle?.contains(event.target)
            ) return;

            const block = findBlockFromElement(event.target);
            if (block) showBlock(block);
        }, true);
    }

    // ========================================
    // TẠO KHỐI
    // Các giá trị số mặc định được đặt thành 0.
    // ========================================

    function createBlock(type, x, y) {
        const ws = workspace();

        if (!ws) {
            setStatus("Không tìm thấy workspace.");
            return "";
        }

        type = String(type || "").trim();

        if (!type || !SB.Blocks?.[type]) {
            setStatus("Type không tồn tại: " + type);
            return "";
        }

        try {
            const block = ws.newBlock(type);
            block.initSvg();

            // Đặt các trường số trực tiếp của khối cha về 0.
            for (const input of block.inputList || []) {
                for (const field of input.fieldRow || []) {
                    if (
                        field &&
                        typeof field.getValue === "function" &&
                        field.getValue() !== "" &&
                        Number.isFinite(Number(field.getValue()))
                    ) {
                        try {
                            field.setValue("0");
                        } catch (_) {
                            // Bỏ qua trường không cho phép thay đổi.
                        }
                    }
                }
            }

            // Trong Scratch, giá trị số của input thường nằm trong
            // một shadow block math_number (ví dụ ô "10 bước").
            // Đưa các shadow block số này về 0.
            const numericShadowTypes = new Set([
                "math_number",
                "math_integer",
                "math_whole_number",
                "math_positive_number",
                "math_angle"
            ]);

            for (const input of block.inputList || []) {
                const child = input.connection?.targetBlock();

                if (
                    !child ||
                    !child.isShadow?.() ||
                    !numericShadowTypes.has(child.type)
                ) {
                    continue;
                }

                const numberField =
                    child.getField("NUM") ||
                    child.getField("VALUE");

                if (numberField) {
                    try {
                        numberField.setValue("0");
                        child.render();
                    } catch (_) {
                        // Bỏ qua shadow block không thể chỉnh sửa.
                    }
                }
            }

            block.render();
            block.moveBy(Number(x) || 0, Number(y) || 0);

            lastId = block.id;

            setStatus(`Đã tạo ${type} với giá trị số mặc định bằng 0; ID: ${block.id}`);
            showBlock(block);

            return block.id;
        } catch (error) {
            setStatus("Tạo khối thất bại: " + error.message);
            return "";
        }
    }

    // ========================================
    // ĐẶT TRƯỜNG / INPUT
    // ========================================

    function setField(blockId, fieldName, value) {
        const block = getBlock(blockId);

        if (!block) {
            setStatus("Không tìm thấy khối: " + blockId);
            return;
        }

        fieldName = String(fieldName || "").trim();

        const field = block.getField(fieldName);

        if (field) {
            try {
                field.setValue(String(value));
                block.render();
                setStatus(`Đã đặt ${fieldName} = ${value}`);
            } catch (error) {
                setStatus("Không đặt được trường: " + error.message);
            }
            return;
        }

        const input = block.getInput(fieldName);

        if (!input?.connection) {
            setStatus(`Không tìm thấy trường/input "${fieldName}".`);
            return;
        }

        let child = input.connection.targetBlock();
        const str = String(value);
        const isNumber = str.trim() !== "" && Number.isFinite(Number(str));

        if (!child) {
            const childType = isNumber ? "math_number" : "text";

            if (!SB.Blocks?.[childType]) {
                setStatus("Không tìm thấy loại khối con " + childType);
                return;
            }

            try {
                child = block.workspace.newBlock(childType);

                const childField = isNumber ? "NUM" : "TEXT";

                if (child.getField(childField)) {
                    child.setFieldValue(str, childField);
                }

                child.initSvg();
                child.render();

                if (!child.outputConnection) {
                    child.dispose(true);
                    setStatus("Khối con không có outputConnection.");
                    return;
                }

                input.connection.connect(child.outputConnection);
                lastId = child.id;

                setStatus("Đã tạo và nối khối con: " + child.id);
            } catch (error) {
                setStatus("Tạo khối con thất bại: " + error.message);
            }

            return;
        }

        for (const name of ["NUM", "TEXT"]) {
            if (child.getField(name)) {
                try {
                    child.setFieldValue(str, name);
                    child.render();
                    setStatus(`Đã sửa ${name} của khối con ${child.id}`);
                } catch (error) {
                    setStatus("Sửa khối con thất bại: " + error.message);
                }
                return;
            }
        }

        setStatus("Khối con không có trường NUM hoặc TEXT để sửa.");
    }

    // ========================================
    // NỐI INPUT
    // ========================================

    function connectInput(parentId, childId, inputName) {
        const parent = getBlock(parentId);
        const child = getBlock(childId);

        if (!parent || !child) {
            setStatus("Không tìm thấy khối cha/con.");
            return;
        }

        const input = parent.getInput(String(inputName || "").trim());

        if (!input?.connection) {
            setStatus(`Không tìm thấy input "${inputName}".`);
            return;
        }

        let childConnection = child.outputConnection;

        if (
            input.type === SB.NEXT_STATEMENT ||
            input.type === 3
        ) {
            childConnection = child.previousConnection;
        }

        if (!childConnection) {
            setStatus("Khối con không có kết nối tương thích.");
            return;
        }

        try {
            input.connection.connect(childConnection);
            parent.render();
            child.render();
            setStatus(`Đã nối ${childId} vào ${parentId}.${inputName}`);
        } catch (error) {
            setStatus("Nối khối thất bại: " + error.message);
        }
    }

    // ========================================
    // NỐI KHỐI TIẾP THEO
    // ========================================

    function connectNext(parentId, childId) {
        const parent = getBlock(parentId);
        const child = getBlock(childId);

        if (!parent || !child) {
            setStatus("Không tìm thấy khối cha/con.");
            return;
        }

        if (!parent.nextConnection || !child.previousConnection) {
            setStatus("Hai khối không có kết nối next/previous.");
            return;
        }

        try {
            parent.nextConnection.connect(child.previousConnection);
            parent.render();
            child.render();
            setStatus(`Đã nối ${childId} sau ${parentId}`);
        } catch (error) {
            setStatus("Nối next thất bại: " + error.message);
        }
    }

    // ========================================
    // DI CHUYỂN KHỐI
    // ========================================

    function moveBlock(id, x, y) {
        const block = getBlock(id);

        if (!block) {
            setStatus("Không tìm thấy khối: " + id);
            return;
        }

        try {
            const pos = block.getRelativeToSurfaceXY();

            block.moveBy(
                (Number(x) || 0) - pos.x,
                (Number(y) || 0) - pos.y
            );

            setStatus("Đã di chuyển khối " + id);
        } catch (error) {
            setStatus("Di chuyển thất bại: " + error.message);
        }
    }

    // ========================================
    // XÓA KHỐI
    // ========================================

    function deleteBlock(id) {
        const block = getBlock(id);

        if (!block) {
            setStatus("Không tìm thấy khối: " + id);
            return;
        }

        try {
            block.dispose(true);

            if (lastId === String(id)) lastId = "";
            if (hoveredId === String(id)) hoveredId = "";

            setStatus("Đã xóa khối " + id);
        } catch (error) {
            setStatus("Xóa khối thất bại: " + error.message);
        }
    }

    // ========================================
    // EXTENSION
    // ========================================

    class WorkspaceEditor {
        constructor() {
            createPanel();
        }

        getInfo() {
            return {
                id: EXT,
                name: "Workspace Editor",
                color1: "#5145a8",
                color2: "#413687",
                color3: "#30285f",

                blocks: [
                    {
                        opcode: "create",
                        blockType: Scratch.BlockType.REPORTER,
                        text: "tạo khối [TYPE] x [X] y [Y]",
                        arguments: {
                            TYPE: {
                                type: Scratch.ArgumentType.STRING,
                                menu: "blockTypes",
                                defaultValue: "motion_movesteps"
                            },
                            X: {
                                type: Scratch.ArgumentType.NUMBER,
                                defaultValue: 0
                            },
                            Y: {
                                type: Scratch.ArgumentType.NUMBER,
                                defaultValue: 0
                            }
                        }
                    },
                    {
                        opcode: "lastId",
                        blockType: Scratch.BlockType.REPORTER,
                        text: "ID khối vừa tạo"
                    },
                    {
                        opcode: "hoveredId",
                        blockType: Scratch.BlockType.REPORTER,
                        text: "ID khối đang rê chuột"
                    },
                    {
                        opcode: "setField",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "đặt trường [FIELD] của khối [ID] thành [VALUE]",
                        arguments: {
                            FIELD: {
                                type: Scratch.ArgumentType.STRING,
                                menu: "fieldChoices",
                                defaultValue: "STEPS"
                            },
                            ID: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            },
                            VALUE: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: "10"
                            }
                        }
                    },
                    {
                        opcode: "connectInput",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "nối khối [CHILD] vào input [INPUT] của khối [PARENT]",
                        arguments: {
                            CHILD: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            },
                            INPUT: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: "STEPS"
                            },
                            PARENT: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            }
                        }
                    },
                    {
                        opcode: "connectNext",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "nối khối [CHILD] sau khối [PARENT]",
                        arguments: {
                            CHILD: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            },
                            PARENT: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            }
                        }
                    },
                    {
                        opcode: "moveBlock",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "di chuyển khối [ID] đến x [X] y [Y]",
                        arguments: {
                            ID: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            },
                            X: {
                                type: Scratch.ArgumentType.NUMBER,
                                defaultValue: 0
                            },
                            Y: {
                                type: Scratch.ArgumentType.NUMBER,
                                defaultValue: 0
                            }
                        }
                    },
                    {
                        opcode: "deleteBlock",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "xóa khối [ID]",
                        arguments: {
                            ID: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            }
                        }
                    },
                    {
                        opcode: "deleteLast",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "xóa khối vừa tạo"
                    },
                    {
                        opcode: "blockInfo",
                        blockType: Scratch.BlockType.REPORTER,
                        text: "thông tin khối [ID]",
                        arguments: {
                            ID: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            }
                        }
                    },
                    {
                        opcode: "copyId",
                        blockType: Scratch.BlockType.COMMAND,
                        text: "sao chép ID khối [ID]",
                        arguments: {
                            ID: {
                                type: Scratch.ArgumentType.STRING,
                                defaultValue: ""
                            }
                        }
                    },
                    {
                        opcode: "status",
                        blockType: Scratch.BlockType.REPORTER,
                        text: "trạng thái Workspace Editor"
                    }
                ],

                menus: {
                    blockTypes: {
                        acceptReporters: false,
                        items: "getBlockTypes"
                    },
                    fieldChoices: {
                        acceptReporters: false,
                        items: "getAllFieldsMenu"
                    }
                }
            };
        }

        getBlockTypes() {
            return getBlockTypes();
        }

        getAllFieldsMenu() {
            return getAllFieldsMenu();
        }

        create(args) {
            return createBlock(args.TYPE, args.X, args.Y);
        }

        lastId() {
            return lastId;
        }

        hoveredId() {
            return hoveredId;
        }

        setField(args) {
            setField(args.ID, args.FIELD, args.VALUE);
        }

        connectInput(args) {
            connectInput(args.PARENT, args.CHILD, args.INPUT);
        }

        connectNext(args) {
            connectNext(args.PARENT, args.CHILD);
        }

        moveBlock(args) {
            moveBlock(args.ID, args.X, args.Y);
        }

        deleteBlock(args) {
            deleteBlock(args.ID);
        }

        deleteLast() {
            if (lastId) {
                deleteBlock(lastId);
            } else {
                setStatus("Chưa có khối nào được tạo.");
            }
        }

        blockInfo(args) {
            const block = getBlock(args.ID);

            if (!block) return "Không tìm thấy khối.";

            return JSON.stringify({
                id: block.id,
                type: block.type,
                fields: getFields(block),
                inputs: getInputs(block)
            });
        }

        copyId(args) {
            copyText(args.ID);
        }

        status() {
            return statusText;
        }
    }

    Scratch.extensions.register(new WorkspaceEditor());
})(Scratch);
