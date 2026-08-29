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
//custom html
                {
    opcode: "setHTML",

    blockType:
        Scratch.BlockType.COMMAND,

    text:
        "đặt HTML trang web thành [HTML]",

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
                        "khi nhận tin nhắn",

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
                        "khi gửi tin nhắn",

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
                        "tạo trang web"
                },


                // =========================
                // URL
                // =========================

                {
                    opcode: "url",

                    blockType:
                        Scratch.BlockType.REPORTER,

                    text:
                        "URL trang web"
                },


                // =========================
                // GỬI TIN NHẮN
                // =========================

                {
                    opcode: "send",

                    blockType:
                        Scratch.BlockType.COMMAND,

                    text:
                        "gửi [DATA] tới trang web",

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
                        "tin nhắn từ trang web"
                },


                // =========================
                // WEB CONNECTED
                // =========================

                {
                    opcode: "connected",

                    blockType:
                        Scratch.BlockType.BOOLEAN,

                    text:
                        "web đã kết nối?"
                },


                // =========================
                // ĐÓNG WEB
                // =========================

                {
                    opcode: "close",

                    blockType:
                        Scratch.BlockType.COMMAND,

                    text:
                        "đóng trang web"
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
            "[WebBridge] Đang tạo room..."
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
                            "[WebBridge] JSON không hợp lệ"
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
                            "[WebBridge] Nhận:",
                            this.lastMessage
                        );


                        // Kích hoạt hat
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
                            "[WebBridge] Room đã đóng"
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
                "[WebBridge] Không có WebSocket"
            );

            return;

        }


        if (!this.room) {

            console.warn(
                "[WebBridge] Không có room"
            );

            return;

        }


        if (
            this.ws.readyState !==
            WebSocket.OPEN
        ) {

            console.warn(
                "[WebBridge] WebSocket chưa connected"
            );

            return;

        }


        const data =
            String(
                args.DATA
            );


        console.log(
            "[WebBridge] Gửi:",
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

setHTML(args) {

    if (!this.ws) {

        console.warn(
            "[WebBridge] Chưa kết nối server"
        );

        return;
    }

    if (!this.room) {

        console.warn(
            "[WebBridge] Chưa có room"
        );

        return;
    }

    if (
        this.ws.readyState !==
        WebSocket.OPEN
    ) {

        console.warn(
            "[WebBridge] WebSocket chưa kết nối"
        );

        return;
    }

    const html =
        String(args.HTML);

    console.log(
        "[WebBridge] Đang cập nhật HTML..."
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

        return (
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
                "[WebBridge] Không có room để đóng"
            );

            return;

        }


        console.log(
            "[WebBridge] Đang đóng room:",
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
