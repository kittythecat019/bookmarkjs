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
        // CREATE ROOM
        // =========================

        create() {

            console.log(
                "[WebBridge] Creating room..."
            );


            // Đóng WebSocket cũ

            if (this.ws) {

                try {

                    this.ws.close();

                } catch {}

            }


            this.ws = null;

            this.room = "";

            this.webUrl = "";

            this.lastMessage = "";


            try {

                // =========================
                // KẾT NỐI SERVER
                // =========================

                this.ws =
                    new WebSocket(
                        "ws://localhost:3000"
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

                            this.room =
                                data.room;


                            this.webUrl =
                                "http-server-web-bridge" +
                                data.url;


                            console.log(
                                "[WebBridge] Room:",
                                this.room
                            );


                            console.log(
                                "[WebBridge] URL:",
                                this.webUrl
                            );


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


                            // Kích hoạt:
                            // "khi nhận tin nhắn"

                            const result =
                                Scratch.vm.runtime.startHats(
                                    "webbridge_whenReceive"
                                );


                            console.log(
                                "[WebBridge] RECEIVE HAT:",
                                result
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

                    };


                // =========================
                // CLOSE
                // =========================

                this.ws.onclose = () => {

                    console.log(
                        "[WebBridge] WebSocket closed"
                    );

                };


            } catch (error) {

                console.error(
                    "[WebBridge] Error:",
                    error
                );

            }

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

            const result =
                Scratch.vm.runtime.startHats(
                    "webbridge_whenSend"
                );


            console.log(
                "[WebBridge] SEND HAT:",
                result
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
