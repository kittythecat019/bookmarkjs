(() => {
    const oldFetch = window.fetch;

    window.fetch = function(resource, options) {
        let url = typeof resource === "string"
            ? resource
            : resource.url;

        if (url.startsWith(
            "https://translate-service.scratch.mit.edu/translate"
        )) {
            const u = new URL(url);

            resource =
                "https://test-project-y7x1.onrender.com/translate" +
                u.search;
        }

        return oldFetch(resource, options);
    };
})();
