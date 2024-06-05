(function () {
    var hljsGrammar = (function () {
        /*
        Language: Adams Message
        Description: Adams message files
        Website: ...
        Category: scientific
        */
        function adams_msg(hljs) {
            return {
                name: "Adams msg",
                aliases: ["adams_msg", "msg"],
                contains: [
                    {
                        className: "numerics",
                        begin: "\\b[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?(d|D)?\\b",
                    },
                    {
                        className: "commands",

                        begin: "^ command: .*$",
                        contains: [
                            {
                                className: "strong",
                                begin: " command",
                                end: ":",
                            },
                            {
                                className: "comment",
                                begin: "(?i)(?<= command: )\\s*!.*$",
                                end: "$",
                            },
                            {
                                className: "code",
                                begin: "(?i)(?<= command: )\\s*.*",
                                end: "$",
                            },
                        ],
                    },
                    {
                        className: "messages",
                        contains: [
                            {
                                className: "deletion",
                                begin: "(?i)-+ start: error -+",
                                end: "(?i)-+ end: error -+",
                            },
                            {
                                className: "addition",
                                begin: "(?i)^.*solution converged.*",
                                end: "$",
                            },
                            {
                                className: "deletion",
                                begin: "(?i)^.*solution failed to converge.*$",
                                end: "$",
                            },
                        ],
                    },
                ],
            };
        }

        return adams_msg;
    })();

    hljs.registerLanguage("adams_msg", hljsGrammar);
})
