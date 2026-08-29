(function (Scratch) {
    "use strict";

    if (!Scratch.extensions.unsandboxed) {
        throw new Error(
            "Web Bridge must be run with 'Run without sandbox'."
        );
    }

    class WebBridge {

        constructor() {

            this.ws = null;

            this.room = "";

            this.webUrl = "";

            this.lastMessage = "";

            this.created = false;

        }


        getInfo() {

            return {

                id: "webbridge",

                name: "Web Bridge",

                color1: "#4C97FF",

                color2: "#3373CC",

                blocks: [

                    // =========================
                    // CUSTOM HTML
                    // =========================

                    {
                        opcode: "setHTML",

                        blockType:
                            Scratch.BlockType.COMMAND,

                        text:
                            Scratch.translate(
                                "set web page HTML to [HTML]"
                            ),

                        arguments: {

                            HTML: {

                                type:
                                    Scratch.ArgumentType.STRING,

                                defaultValue:
                                    "<h1>Hello!</h1>"

                            }

                        }

                    },


                    // =========================
                    // KHI NHẬN TIN NHẮN
                    // =========================

                    {
                        opcode: "whenReceive",

                        blockType:
                            Scratch.BlockType.EVENT,

                        text:
                            Scratch.translate(
                                "when message received"
                            ),

                        isEdgeActivated:
                            false

                    },


                    // =========================
                    // KHI GỬI TIN NHẮN
                    // =========================

                    {
                        opcode: "whenSend",

                        blockType:
                            Scratch.BlockType.EVENT,

                        text:
                            Scratch.translate(
                                "when message sent"
                            ),

                        isEdgeActivated:
                            false

                    },


                    // =========================
                    // KHI WEB ĐƯỢC TẠO
                    // =========================

                    {
                        opcode: "whenCreated",

                        blockType:
                            Scratch.BlockType.EVENT,

                        text:
                            Scratch.translate(
                                "when web page created"
                            ),

                        isEdgeActivated:
                            false

                    },


                    // =========================
                    // WEB ĐƯỢC TẠO?
                    // =========================

                    {
                        opcode: "isCreated",

                        blockType:
                            Scratch.BlockType.BOOLEAN,

                        text:
                            Scratch.translate(
                                "web page created?"
                            )

                    },


                    // =========================
                    // TẠO TRANG WEB
                    // =========================

                    {
                        opcode: "create",

                        blockType:
                            Scratch.BlockType.COMMAND,

                        text:
                            Scratch.translate(
                                "create web page"
                            )

                    },


                    // =========================
                    // URL
                    // =========================

                    {
                        opcode: "url",

                        blockType:
                            Scratch.BlockType.REPORTER,

                        text:
                            Scratch.translate(
                                "web page URL"
                            )

                    },


                    // =========================
                    // GỬI TIN NHẮN
                    // =========================

                    {
                        opcode: "send",

                        blockType:
                            Scratch.BlockType.COMMAND,

                        text:
                            Scratch.translate(
                                "send [DATA] to web page"
                            ),

                        arguments: {

                            DATA: {

                                type:
                                    Scratch.ArgumentType.STRING,

                                defaultValue:
                                    "Hello!"

                            }

                        }

                    },


                    // =========================
                    // TIN NHẮN TỪ WEB
                    // =========================

                    {
                        opcode: "message",

                        blockType:
                            Scratch.BlockType.REPORTER,

                        text:
                            Scratch.translate(
                                "message from web page"
                            )

                    },


                    // =========================
                    // WEB CONNECTED
                    // =========================

                    {
                        opcode: "connected",

                        blockType:
                            Scratch.BlockType.BOOLEAN,

                        text:
                            Scratch.translate(
                                "web page connected?"
                            )

                    },


                    // =========================
                    // ĐÓNG WEB
                    // =========================

                    {
                        opcode: "close",

                        blockType:
                            Scratch.BlockType.COMMAND,

                        text:
                            Scratch.translate(
                                "close web page"
                            )

                    }

                ]

            };

        }


        // =========================
        // HAT: NHẬN
        // =========================

        whenReceive() {

            return false;

        }


        // =========================
        // HAT: GỬI
        // =========================

        whenSend() {

            return false;

        }


        // =========================
        // HAT: WEB ĐƯỢC TẠO
        // =========================

        whenCreated() {

            return false;

        }


        // =========================
        // WEB ĐƯỢC TẠO?
        // =========================

        isCreated() {

            return this.created;

        }


        // =========================
        // CREATE ROOM
        // =========================

        async create() {

            console.log(
                "[WebBridge] Creating room..."
            );


            // =========================
            // ĐÓNG WEBSOCKET CŨ
            // =========================

            if (this.ws) {

                try {

                    this.ws.close();

                } catch {}

            }


            // =========================
            // RESET
            // =========================

            this.ws = null;

            this.room = "";

            this.webUrl = "";

            this.lastMessage = "";

            this.created = false;


            // =========================
            // CHỜ TỐI ĐA 3 GIÂY
            // =========================

            await new Promise((resolve) => {

                let finished = false;


                const finish = () => {

                    if (finished) {
                        return;
                    }

                    finished = true;

                    clearTimeout(timeout);

                    resolve();

                };


                // =========================
                // TIMEOUT
                // =========================

                const timeout =
                    setTimeout(() => {

                        console.warn(
                            "[WebBridge] Không tạo được web sau 3 giây"
                        );


                        this.created = false;

                        this.room = "";

                        this.webUrl = "";


                        if (this.ws) {

                            try {

                                this.ws.close();

                            } catch {}

                        }


                        this.ws = null;


                        finish();

                    }, 3000);


                try {

                    // =========================
                    // KẾT NỐI RENDER
                    // =========================

                    this.ws =
                        new WebSocket(
                            "wss://extension-web-server-8fkf.onrender.com"
                        );


                    // =========================
                    // OPEN
                    // =========================

                    this.ws.onopen = () => {

                        console.log(
                            "[WebBridge] WebSocket connected"
                        );


                        this.ws.send(
                            JSON.stringify({

                                type:
                                    "create"

                            })
                        );

                    };


                    // =========================
                    // MESSAGE
                    // =========================

                    this.ws.onmessage =
                        (event) => {

                            console.log(
                                "[WebBridge] Server:",
                                event.data
                            );


                            let data;


                            try {

                                data =
                                    JSON.parse(
                                        event.data
                                    );

                            } catch {

                                console.error(
                                    "[WebBridge] Invalid JSON"
                                );

                                return;

                            }


                            // =========================
                            // ROOM CREATED
                            // =========================

                            if (
                                data.type ===
                                "created"
                            ) {

                                if (finished) {
                                    return;
                                }


                                this.room =
                                    data.room;


                                this.webUrl =
                                    "https://extension-web-server-8fkf.onrender.com" +
                                    data.url;


                                this.created =
                                    true;


                                console.log(
                                    "[WebBridge] Room:",
                                    this.room
                                );


                                console.log(
                                    "[WebBridge] URL:",
                                    this.webUrl
                                );


                                // =========================
                                // KHI WEB ĐƯỢC TẠO
                                // =========================

                                Scratch.vm.runtime.startHats(
                                    "webbridge_whenCreated"
                                );


                                finish();

                                return;

                            }


                            // =========================
                            // NHẬN TIN NHẮN
                            // =========================

                            if (
                                data.type ===
                                "message"
                            ) {

                                this.lastMessage =
                                    String(
                                        data.data
                                    );


                                console.log(
                                    "[WebBridge] Received:",
                                    this.lastMessage
                                );


                                Scratch.vm.runtime.startHats(
                                    "webbridge_whenReceive"
                                );


                                return;

                            }


                            // =========================
                            // ROOM CLOSED
                            // =========================

                            if (
                                data.type ===
                                "closed"
                            ) {

                                console.log(
                                    "[WebBridge] Room closed"
                                );


                                this.room = "";

                                this.webUrl = "";

                                this.lastMessage = "";

                                this.created = false;


                                return;

                            }


                            // =========================
                            // SERVER ERROR
                            // =========================

                            if (
                                data.type ===
                                "error"
                            ) {

                                console.error(
                                    "[WebBridge] Server error:",
                                    data.message
                                );


                                this.created = false;

                                this.room = "";

                                this.webUrl = "";


                                finish();

                                return;

                            }

                        };


                    // =========================
                    // ERROR
                    // =========================

                    this.ws.onerror =
                        (error) => {

                            console.error(
                                "[WebBridge] WebSocket error:",
                                error
                            );


                            this.created = false;

                            this.room = "";

                            this.webUrl = "";


                            finish();

                        };


                    // =========================
                    // CLOSE
                    // =========================

                    this.ws.onclose = () => {

                        console.log(
                            "[WebBridge] WebSocket closed"
                        );


                        if (!this.created) {

                            this.room = "";

                            this.webUrl = "";

                        }

                    };


                } catch (error) {

                    console.error(
                        "[WebBridge] Error:",
                        error
                    );


                    this.created = false;

                    this.room = "";

                    this.webUrl = "";


                    finish();

                }

            });

        }


        // =========================
        // URL
        // =========================

        url() {

            return this.webUrl;

        }


        // =========================
        // SEND
        // =========================

        send(args) {

            if (!this.ws) {

                console.warn(
                    "[WebBridge] No WebSocket"
                );

                return;

            }


            if (!this.room) {

                console.warn(
                    "[WebBridge] No room"
                );

                return;

            }


            if (
                this.ws.readyState !==
                WebSocket.OPEN
            ) {

                console.warn(
                    "[WebBridge] WebSocket not connected"
                );

                return;

            }


            const data =
                String(
                    args.DATA
                );


            console.log(
                "[WebBridge] Sending:",
                data
            );


            this.ws.send(
                JSON.stringify({

                    type:
                        "message",

                    room:
                        this.room,

                    role:
                        "scratch",

                    data:
                        data

                })
            );


            // =========================
            // KÍCH HOẠT HAT GỬI
            // =========================

            Scratch.vm.runtime.startHats(
                "webbridge_whenSend"
            );

        }


        // =========================
        // SET HTML
        // =========================

        setHTML(args) {

            if (!this.ws) {

                console.warn(
                    "[WebBridge] Server not connected"
                );

                return;

            }


            if (!this.room) {

                console.warn(
                    "[WebBridge] No room"
                );

                return;

            }


            if (
                this.ws.readyState !==
                WebSocket.OPEN
            ) {

                console.warn(
                    "[WebBridge] WebSocket not connected"
                );

                return;

            }


            const html =
                String(
                    args.HTML
                );


            console.log(
                "[WebBridge] Updating HTML..."
            );


            this.ws.send(
                JSON.stringify({

                    type:
                        "setHTML",

                    room:
                        this.room,

                    html:
                        html

                })
            );

        }


        // =========================
        // MESSAGE
        // =========================

        message() {

            return this.lastMessage;

        }


        // =========================
        // CONNECTED
        // =========================

        connected() {

            return !!(
                this.ws &&
                this.ws.readyState ===
                WebSocket.OPEN
            );

        }


        // =========================
        // CLOSE ROOM
        // =========================

        close() {

            if (!this.ws) {

                return;

            }


            if (!this.room) {

                console.warn(
                    "[WebBridge] No room to close"
                );

                return;

            }


            console.log(
                "[WebBridge] Closing room:",
                this.room
            );


            this.ws.send(
                JSON.stringify({

                    type:
                        "close",

                    room:
                        this.room

                })
            );


            this.room = "";

            this.webUrl = "";

            this.lastMessage = "";

            this.created = false;


            try {

                this.ws.close();

            } catch {}

        }

    }


    // =========================
    // REGISTER EXTENSION
    // =========================

    Scratch.extensions.register(
        new WebBridge()
    );

})(Scratch);
