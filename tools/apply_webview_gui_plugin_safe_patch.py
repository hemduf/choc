from pathlib import Path

HEADER = Path("choc/gui/choc_WebView.h")
text = HEADER.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    text = text.replace(old, new, 1)


# -----------------------------------------------------------------------------
# Public opt-in: defer the first resource-backed navigation until clients have
# registered document-start scripts. Default behaviour remains upstream CHOC.
replace_once(
    "        std::function<void(choc::ui::WebView&)> webviewIsReady;",
    """        std::function<void(choc::ui::WebView&)> webviewIsReady;

        /// When resource-backed content is used, defer CHOC's automatic first
        /// navigation. This is useful to install document-start security scripts
        /// before any untrusted page can be loaded. The default preserves the
        /// upstream behaviour.
        bool deferInitialResourceNavigation = false;""",
    "WebView::Options bootstrap control",
)

for label, old, new in (
    (
        "Linux startup navigation",
        """            webkit_web_context_register_uri_scheme (webviewContext, getURIScheme (options).c_str(), onResourceRequested, this, nullptr);
            navigate ({});""",
        """            webkit_web_context_register_uri_scheme (webviewContext, getURIScheme (options).c_str(), onResourceRequested, this, nullptr);
            if (! options.deferInitialResourceNavigation)
                navigate ({});""",
    ),
    (
        "macOS startup navigation",
        """        if (options->fetchResource)
            navigate ({});""",
        """        if (options->fetchResource && ! options->deferInitialResourceNavigation)
            navigate ({});""",
    ),
    (
        "Windows startup navigation",
        """        if (options.fetchResource)
            navigate ({});""",
        """        if (options.fetchResource && ! options.deferInitialResourceNavigation)
            navigate ({});""",
    ),
):
    replace_once(old, new, label)

# -----------------------------------------------------------------------------
# Linux/WebKitGTK lifetime and ownership fixes.
replace_once(
    """        webviewContext = webkit_web_context_new();
        g_object_ref_sink (G_OBJECT (webviewContext));
        webview = webkit_web_view_new_with_context (webviewContext);""",
    """        webviewContext = webkit_web_context_new();
        webview = webkit_web_view_new_with_context (webviewContext);""",
    "Linux WebKit context ownership",
)

replace_once(
    """    ~Pimpl()
    {
        deletionChecker->deleted = true;

        if (signalHandlerID != 0 && webview != nullptr)
            g_signal_handler_disconnect (manager, signalHandlerID);

        g_clear_object (&webview);
        g_clear_object (&webviewContext);
    }""",
    """    ~Pimpl()
    {
        deletionChecker->deleted = true;

        if (manager != nullptr)
        {
            if (signalHandlerID != 0
                && g_signal_handler_is_connected (manager, signalHandlerID))
                g_signal_handler_disconnect (manager, signalHandlerID);

            webkit_user_content_manager_unregister_script_message_handler (manager, \"external\");
            webkit_user_content_manager_remove_all_scripts (manager);
        }

        if (webview != nullptr)
        {
            webkit_web_view_stop_loading (WEBKIT_WEB_VIEW (webview));
            gtk_widget_destroy (GTK_WIDGET (webview));
        }

        signalHandlerID = 0;
        manager = nullptr;
        g_clear_object (&webview);
        g_clear_object (&webviewContext);
    }""",
    "Linux teardown",
)

replace_once(
    """        else
        {
            errorMessage = \"Failed to fetch result\";
        }

        (*completionHandler) (errorMessage, value);""",
    """        else
        {
            if (error != nullptr)
            {
                errorMessage = error->message;
                g_error_free (error);
            }
            else
            {
                errorMessage = \"Failed to fetch result\";
            }
        }

        (*completionHandler) (errorMessage, value);""",
    "Linux JavaScript GError lifetime",
)

replace_once(
    """    bool addInitScript (const std::string& js)
    {
        if (manager != nullptr)
        {
            webkit_user_content_manager_add_script (manager, webkit_user_script_new (js.c_str(),
                                                                                     WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                                                                     WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START,
                                                                                     nullptr, nullptr));
            return true;
        }

        return false;
    }""",
    """    bool addInitScript (const std::string& js)
    {
        if (manager != nullptr)
        {
            auto* script = webkit_user_script_new (js.c_str(),
                                                   WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                                   WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START,
                                                   nullptr, nullptr);

            if (script == nullptr)
                return false;

            webkit_user_content_manager_add_script (manager, script);
            webkit_user_script_unref (script);
            return true;
        }

        return false;
    }""",
    "Linux init-script ownership",
)

replace_once(
    """                        soup_message_headers_append (headers, \"Cache-Control\", \"no-store\");
                        soup_message_headers_append (headers, \"Access-Control-Allow-Origin\", \"*\");""",
    """                        soup_message_headers_append (headers, \"Cache-Control\", \"no-store\");
                       #if defined (CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN)
                        soup_message_headers_append (headers, \"Access-Control-Allow-Origin\", CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN);
                       #else
                        soup_message_headers_append (headers, \"Access-Control-Allow-Origin\", \"*\");
                       #endif
                       #if defined (CHOC_WEBVIEW_CONTENT_SECURITY_POLICY)
                        soup_message_headers_append (headers, \"Content-Security-Policy\", CHOC_WEBVIEW_CONTENT_SECURITY_POLICY);
                       #endif""",
    "Linux native response policy hooks",
)

# -----------------------------------------------------------------------------
# macOS/WKWebView unload safety. These fixes are generic; the heavier shared_ptr
# and class-static avoidance is enabled only by CHOC_WEBVIEW_PLUGIN_SAFE.
replace_once(
    """    ~Pimpl()
    {
        CHOC_AUTORELEASE_BEGIN
        deletionChecker->deleted = true;
        objc_setAssociatedObject (delegate, \"choc_webview\", nil, OBJC_ASSOCIATION_ASSIGN);
        objc_setAssociatedObject (webview, \"choc_webview\", nil, OBJC_ASSOCIATION_ASSIGN);
        objc::call<void> (webview, \"release\");
        webview = {};
        objc::call<void> (manager, \"removeScriptMessageHandlerForName:\", objc::getNSString (\"external\"));
        objc::call<void> (manager, \"release\");
        manager = {};
        objc::call<void> (delegate, \"release\");
        delegate = {};
        CHOC_AUTORELEASE_END
    }""",
    """    ~Pimpl()
    {
        CHOC_AUTORELEASE_BEGIN
        deletionChecker->deleted = true;
        objc_setAssociatedObject (delegate, &chocWebViewAssociatedObjectKey, nil, OBJC_ASSOCIATION_ASSIGN);
        objc_setAssociatedObject (webview, &chocWebViewAssociatedObjectKey, nil, OBJC_ASSOCIATION_ASSIGN);

        // Detach every callback entry point while an unloadable client image is
        // still resident, then stop outstanding work before releasing the view.
        objc::call<void> (webview, \"setUIDelegate:\", (id) nil);
        objc::call<void> (webview, \"setNavigationDelegate:\", (id) nil);
        objc::call<void> (manager, \"removeScriptMessageHandlerForName:\", objc::getNSString (\"external\"));
        objc::call<void> (webview, \"stopLoading\");

        objc::call<void> (webview, \"release\");
        webview = {};
        objc::call<void> (manager, \"release\");
        manager = {};
        objc::call<void> (delegate, \"release\");
        delegate = {};
        CHOC_AUTORELEASE_END
    }""",
    "macOS teardown",
)

replace_once(
    """#elif CHOC_APPLE

#include \"../platform/choc_ObjectiveCHelpers.h\"

struct choc::ui::WebView::Pimpl""",
    """#elif CHOC_APPLE

#include \"../platform/choc_ObjectiveCHelpers.h\"

static char chocWebViewAssociatedObjectKey;

struct choc::ui::WebView::Pimpl""",
    "macOS associated-object key storage",
)

# Replace the remaining associated-object keys with an internal-linkage address.
remaining_keys = text.count('"choc_webview"')
if remaining_keys != 3:
    raise RuntimeError(f"macOS associated-object keys: expected 3 remaining anchors, found {remaining_keys}")
text = text.replace('"choc_webview"', '&chocWebViewAssociatedObjectKey')

replace_once(
    """    static constexpr const char* postMessageFn = \"window.webkit.messageHandlers.external.postMessage\";

    bool stillInitialising() const  { return false; }
    void* getViewHandle() const     { return (CHOC_OBJC_CAST_BRIDGED void*) webview; }

    std::shared_ptr<DeletionChecker> deletionChecker { std::make_shared<DeletionChecker>() };""",
    """    static constexpr const char* postMessageFn = \"window.webkit.messageHandlers.external.postMessage\";

    bool stillInitialising() const  { return false; }
    void* getViewHandle() const     { return (CHOC_OBJC_CAST_BRIDGED void*) webview; }

   #if defined (CHOC_WEBVIEW_PLUGIN_SAFE) && CHOC_WEBVIEW_PLUGIN_SAFE
    struct PluginDeletionCheckerRef
    {
        struct Control
        {
            DeletionChecker checker;
            std::size_t references = 1;
        };

        PluginDeletionCheckerRef() : control (new Control()) {}
        PluginDeletionCheckerRef (const PluginDeletionCheckerRef& other) noexcept : control (other.control)
        {
            if (control != nullptr)
                ++control->references;
        }
        PluginDeletionCheckerRef& operator= (const PluginDeletionCheckerRef&) = delete;
        PluginDeletionCheckerRef (PluginDeletionCheckerRef&&) = delete;
        PluginDeletionCheckerRef& operator= (PluginDeletionCheckerRef&&) = delete;
        ~PluginDeletionCheckerRef()
        {
            if (control != nullptr && --control->references == 0)
                delete control;
        }
        DeletionChecker* operator->() const noexcept
        {
            return control != nullptr ? std::addressof (control->checker) : nullptr;
        }
        Control* control = nullptr;
    };

    PluginDeletionCheckerRef deletionChecker;
   #else
    std::shared_ptr<DeletionChecker> deletionChecker { std::make_shared<DeletionChecker>() };
   #endif""",
    "macOS deletion checker",
)

replace_once(
    """    id allocateWebview()
    {
        static WebviewClass c;
        return objc::call<id> ((id) c.webviewClass, \"alloc\");
    }""",
    """    id allocateWebview()
    {
       #if defined (CHOC_WEBVIEW_PLUGIN_SAFE) && CHOC_WEBVIEW_PLUGIN_SAFE
        if (! options->acceptsFirstMouseClick
            && ! options->enableDefaultClipboardKeyShortcutsInSafari)
            return objc::call<id> ((id) objc_getClass (\"WKWebView\"), \"alloc\");
       #endif

        static WebviewClass c;
        return objc::call<id> ((id) c.webviewClass, \"alloc\");
    }""",
    "macOS WebView class lifetime",
)

replace_once(
    """                id headerKeys[]    = { getNSString (\"Content-Length\"), getNSString (\"Content-Type\"), getNSString (\"Cache-Control\"), getNSString (\"Access-Control-Allow-Origin\") };
                id headerObjects[] = { getNSString (contentLength),    getNSString (mimeType),       getNSString (\"no-store\") ,     getNSString (\"*\") };""",
    """               #if defined (CHOC_WEBVIEW_CONTENT_SECURITY_POLICY)
                id headerKeys[]    = { getNSString (\"Content-Length\"), getNSString (\"Content-Type\"), getNSString (\"Cache-Control\"), getNSString (\"Access-Control-Allow-Origin\"), getNSString (\"Content-Security-Policy\") };
                id headerObjects[] = { getNSString (contentLength),    getNSString (mimeType),       getNSString (\"no-store\"),      getNSString (
                   #if defined (CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN)
                        CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN
                   #else
                        \"*\"
                   #endif
                    ), getNSString (CHOC_WEBVIEW_CONTENT_SECURITY_POLICY) };
               #else
                id headerKeys[]    = { getNSString (\"Content-Length\"), getNSString (\"Content-Type\"), getNSString (\"Cache-Control\"), getNSString (\"Access-Control-Allow-Origin\") };
                id headerObjects[] = { getNSString (contentLength),    getNSString (mimeType),       getNSString (\"no-store\"),      getNSString (
                   #if defined (CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN)
                        CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN
                   #else
                        \"*\"
                   #endif
                    ) };
               #endif""",
    "macOS native response policy hooks",
)

# -----------------------------------------------------------------------------
# Windows/WebView2: make std::wstring dependency explicit.
if "#include <string>" not in text:
    replace_once(
        '#include "../platform/choc_Platform.h"',
        '#include "../platform/choc_Platform.h"\n#include <string>',
        "Windows string include",
    )

# Add the WebView2 async registration completion ABI used to gate first navigation.
replace_once(
    """MIDL_INTERFACE(\"49511172-cc67-4bca-9923-137112f4c4cc\")
ICoreWebView2ExecuteScriptCompletedHandler : public IUnknown
{
public:
    virtual HRESULT STDMETHODCALLTYPE Invoke (HRESULT, LPCWSTR) = 0;
};""",
    """MIDL_INTERFACE(\"b99369f3-9b11-47b5-bc6f-8e7895fcea17\")
ICoreWebView2AddScriptToExecuteOnDocumentCreatedCompletedHandler : public IUnknown
{
public:
    virtual HRESULT STDMETHODCALLTYPE Invoke (HRESULT, LPCWSTR) = 0;
};

MIDL_INTERFACE(\"49511172-cc67-4bca-9923-137112f4c4cc\")
ICoreWebView2ExecuteScriptCompletedHandler : public IUnknown
{
public:
    virtual HRESULT STDMETHODCALLTYPE Invoke (HRESULT, LPCWSTR) = 0;
};""",
    "Windows init-script completion ABI",
)

replace_once(
    """    std::shared_ptr<DeletionChecker> deletionChecker { std::make_shared<DeletionChecker>() };

    bool navigate (const std::string& url)
    {
        if (! coreWebView)
            return false;

        if (url.empty())
            return navigate (defaultURI);

        return coreWebView->Navigate (createUTF16StringFromUTF8 (url).c_str()) == S_OK;
    }

    bool addInitScript (const std::string& script)
    {
        if (! coreWebView)
            return false;

        return coreWebView->AddScriptToExecuteOnDocumentCreated (createUTF16StringFromUTF8 (script).c_str(), nullptr) == S_OK;
    }""",
    """    std::shared_ptr<DeletionChecker> deletionChecker { std::make_shared<DeletionChecker>() };
    std::size_t pendingInitScriptRegistrations = 0;
    bool initScriptRegistrationFailed = false;
    std::optional<std::string> deferredNavigation;

    struct InitScriptCompletedCallback final : public ICoreWebView2AddScriptToExecuteOnDocumentCreatedCompletedHandler
    {
        explicit InitScriptCompletedCallback (Pimpl& p)
            : ownerPimpl (p), deletionCheckerRef (p.deletionChecker) {}

        HRESULT STDMETHODCALLTYPE QueryInterface (REFIID refID, void** result) override
        {
            if (refID == IID { 0xb99369f3, 0x9b11, 0x47b5, { 0xbc, 0x6f, 0x8e, 0x78, 0x95, 0xfc, 0xea, 0x17 } }
                || refID == IID_IUnknown)
            {
                *result = this;
                AddRef();
                return S_OK;
            }
            *result = nullptr;
            return E_NOINTERFACE;
        }

        ULONG STDMETHODCALLTYPE AddRef() override  { return ++refCount; }
        ULONG STDMETHODCALLTYPE Release() override
        {
            const auto newCount = --refCount;
            if (newCount == 0) delete this;
            return newCount;
        }

        HRESULT STDMETHODCALLTYPE Invoke (HRESULT hr, LPCWSTR) override
        {
            if (! deletionCheckerRef->deleted)
                ownerPimpl.initScriptRegistrationCompleted (SUCCEEDED (hr));
            return S_OK;
        }

        Pimpl& ownerPimpl;
        std::shared_ptr<DeletionChecker> deletionCheckerRef;
        std::atomic<ULONG> refCount { 1 };
    };

    bool navigateNow (const std::string& url)
    {
        if (! coreWebView)
            return false;
        const auto& target = url.empty() ? defaultURI : url;
        return coreWebView->Navigate (createUTF16StringFromUTF8 (target).c_str()) == S_OK;
    }

    bool navigate (const std::string& url)
    {
        if (! coreWebView)
            return false;

        if (options.deferInitialResourceNavigation)
        {
            if (initScriptRegistrationFailed)
                return false;
            if (pendingInitScriptRegistrations != 0)
            {
                deferredNavigation = url;
                return true;
            }
        }

        return navigateNow (url);
    }

    void initScriptRegistrationCompleted (bool succeeded)
    {
        if (pendingInitScriptRegistrations == 0)
            return;
        --pendingInitScriptRegistrations;
        if (! succeeded)
        {
            initScriptRegistrationFailed = true;
            deferredNavigation.reset();
            return;
        }
        if (pendingInitScriptRegistrations == 0 && deferredNavigation && ! initScriptRegistrationFailed)
        {
            auto url = std::move (*deferredNavigation);
            deferredNavigation.reset();
            navigateNow (url);
        }
    }

    bool addInitScript (const std::string& script)
    {
        if (! coreWebView)
            return false;

        const auto utf16Script = createUTF16StringFromUTF8 (script);
        if (! options.deferInitialResourceNavigation)
            return coreWebView->AddScriptToExecuteOnDocumentCreated (utf16Script.c_str(), nullptr) == S_OK;

        ++pendingInitScriptRegistrations;
        auto* callback = new InitScriptCompletedCallback (*this);
        const auto hr = coreWebView->AddScriptToExecuteOnDocumentCreated (utf16Script.c_str(), callback);
        callback->Release();
        if (hr != S_OK)
        {
            initScriptRegistrationCompleted (false);
            return false;
        }
        return true;
    }""",
    "Windows navigation/init-script lifecycle",
)

# Resource callback must survive synchronous owner destruction.
replace_once(
    """        try
        {
            if (! coreWebViewEnvironment)
                return E_FAIL;

            COMPtr<ICoreWebView2WebResourceRequest> request;""",
    """        try
        {
           #if defined (CHOC_WEBVIEW_WINDOWS_RESOURCE_CALLBACK_GUARD)
            CHOC_WEBVIEW_WINDOWS_RESOURCE_CALLBACK_GUARD
           #endif

            COMPtr<ICoreWebView2Environment> resourceEnvironment (coreWebViewEnvironment.object);
            if (! resourceEnvironment)
                return E_FAIL;

            const auto resourceDefaultURI = defaultURI;
            const auto resourceSetHTMLURI = setHTMLURI;
            const auto resourcePageHTML = pageHTML;
            auto resourceFetcher = options.fetchResource;
            const auto resourceUserAgent = options.customUserAgent;

            COMPtr<ICoreWebView2WebResourceRequest> request;""",
    "Windows resource callback lifetime prologue",
)

replace_once(
    """            if (auto resource = fetchResourceOrPageHTML (createUTF8FromUTF16 (uri.uri)))
            {""",
    """            const auto resourceURI = createUTF8FromUTF16 (uri.uri);
            std::optional<WebView::Options::Resource> resource;

            if (resourceURI == resourceSetHTMLURI)
            {
                resource = resourcePageHTML;
            }
            else if (resourceFetcher)
            {
                if (resourceDefaultURI.empty() || resourceURI.size() + 1 < resourceDefaultURI.size())
                    return E_FAIL;
                resource = resourceFetcher (resourceURI.substr (resourceDefaultURI.size() - 1));
            }

            if (resource)
            {""",
    "Windows resource callback snapshot fetch",
)

# The first wildcard response at this point is the Windows response block after
# the Linux/macOS replacements above. Narrow it via an opt-in macro and add CSP.
replace_once(
    """                headers.emplace_back (\"Access-Control-Allow-Origin: *\");""",
    """               #if defined (CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN)
                headers.emplace_back (std::string (\"Access-Control-Allow-Origin: \") + CHOC_WEBVIEW_RESOURCE_ALLOW_ORIGIN);
               #else
                headers.emplace_back (\"Access-Control-Allow-Origin: *\");
               #endif
               #if defined (CHOC_WEBVIEW_CONTENT_SECURITY_POLICY)
                headers.emplace_back (std::string (\"Content-Security-Policy: \") + CHOC_WEBVIEW_CONTENT_SECURITY_POLICY);
               #endif""",
    "Windows native response policy hooks",
)

replace_once(
    """                if (! options.customUserAgent.empty())
                    headers.emplace_back (\"User-Agent: \" + options.customUserAgent);""",
    """                if (! resourceUserAgent.empty())
                    headers.emplace_back (\"User-Agent: \" + resourceUserAgent);""",
    "Windows resource user-agent snapshot",
)

for old, new, label in (
    (
        'coreWebViewEnvironment->CreateWebResourceResponse (stream, 200, L"OK", headerString.c_str(), response.getAddress())',
        'resourceEnvironment->CreateWebResourceResponse (stream, 200, L"OK", headerString.c_str(), response.getAddress())',
        "Windows 200 response environment lifetime",
    ),
    (
        'coreWebViewEnvironment->CreateWebResourceResponse (nullptr, 404, L"Not Found", nullptr, response.getAddress())',
        'resourceEnvironment->CreateWebResourceResponse (nullptr, 404, L"Not Found", nullptr, response.getAddress())',
        "Windows 404 response environment lifetime",
    ),
):
    replace_once(old, new, label)

replace_once(
    """        HRESULT STDMETHODCALLTYPE Invoke (ICoreWebView2*, ICoreWebView2WebResourceRequestedEventArgs* args) override
        {
            if (deletionCheckerRef->deleted)
                return E_FAIL;

            return ownerPimpl.onResourceRequested (args);
        }""",
    """        HRESULT STDMETHODCALLTYPE Invoke (ICoreWebView2*, ICoreWebView2WebResourceRequestedEventArgs* args) override
        {
            if (deletionCheckerRef->deleted)
                return E_FAIL;

            AddRef();
            const auto result = ownerPimpl.onResourceRequested (args);
            Release();
            return result;
        }""",
    "Windows resource event handler lifetime",
)

# Message-source policy hook. Without the hook CHOC keeps upstream behaviour.
replace_once(
    """            LPWSTR message = {};
            args->TryGetWebMessageAsString (std::addressof (message));
            ownerPimpl.owner.invokeBinding (createUTF8FromUTF16 (message));
            sender->PostWebMessageAsString (message);
            CoTaskMemFree (message);
            return S_OK;""",
    """           #if defined (CHOC_WEBVIEW_WINDOWS_DISPATCH_MESSAGE)
            return CHOC_WEBVIEW_WINDOWS_DISPATCH_MESSAGE (
                args,
                [&] (LPCWSTR message)
                {
                    ownerPimpl.owner.invokeBinding (createUTF8FromUTF16 (message));
                    sender->PostWebMessageAsString (message);
                });
           #else
            LPWSTR message = {};
            args->TryGetWebMessageAsString (std::addressof (message));
            ownerPimpl.owner.invokeBinding (createUTF8FromUTF16 (message));
            sender->PostWebMessageAsString (message);
            CoTaskMemFree (message);
            return S_OK;
           #endif""",
    "Windows web-message dispatch hook",
)

# Navigation policy is optional and only compiled when a client provides it.
webmessage_interface = """MIDL_INTERFACE(\"57213f19-00e6-49fa-8e07-898ea01ecbd2\")
ICoreWebView2WebMessageReceivedEventHandler : public IUnknown
{
public:
     virtual HRESULT STDMETHODCALLTYPE Invoke(ICoreWebView2 *, ICoreWebView2WebMessageReceivedEventArgs *) = 0;
};"""
navigation_interfaces = """

#if defined (CHOC_WEBVIEW_WINDOWS_HANDLE_NAVIGATION)
struct ICoreWebView2NavigationStartingEventArgs : public IUnknown
{
public:
    virtual HRESULT STDMETHODCALLTYPE get_Uri(LPWSTR*) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_IsUserInitiated(BOOL*) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_IsRedirected(BOOL*) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_RequestHeaders(void**) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_Cancel(BOOL*) = 0;
    virtual HRESULT STDMETHODCALLTYPE put_Cancel(BOOL) = 0;
};

struct ICoreWebView2NavigationStartingEventHandler : public IUnknown
{
public:
    virtual HRESULT STDMETHODCALLTYPE Invoke(ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs*) = 0;
};
#endif"""
replace_once(webmessage_interface, webmessage_interface + navigation_interfaces, "Windows navigation ABI hook")

replace_once(
    """                           public ICoreWebView2WebMessageReceivedEventHandler,
                           public ICoreWebView2PermissionRequestedEventHandler,""",
    """                           public ICoreWebView2WebMessageReceivedEventHandler,
                          #if defined (CHOC_WEBVIEW_WINDOWS_HANDLE_NAVIGATION)
                           public ICoreWebView2NavigationStartingEventHandler,
                          #endif
                           public ICoreWebView2PermissionRequestedEventHandler,""",
    "Windows navigation event base hook",
)

replace_once(
    """            view->add_WebMessageReceived (this, std::addressof (token));
            view->add_PermissionRequested (this, std::addressof (token));""",
    """            view->add_WebMessageReceived (this, std::addressof (token));
           #if defined (CHOC_WEBVIEW_WINDOWS_HANDLE_NAVIGATION)
            view->add_NavigationStarting (
                static_cast<ICoreWebView2NavigationStartingEventHandler*> (this),
                std::addressof (token));
           #endif
            view->add_PermissionRequested (this, std::addressof (token));""",
    "Windows navigation event registration hook",
)

permission_signature = """        HRESULT STDMETHODCALLTYPE Invoke (ICoreWebView2*, ICoreWebView2PermissionRequestedEventArgs* args) override"""
replace_once(
    permission_signature,
    """       #if defined (CHOC_WEBVIEW_WINDOWS_HANDLE_NAVIGATION)
        HRESULT STDMETHODCALLTYPE Invoke (ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs* args) override
        {
            if (deletionCheckerRef->deleted)
                return E_FAIL;
            return CHOC_WEBVIEW_WINDOWS_HANDLE_NAVIGATION (args);
        }
       #endif

""" + permission_signature,
    "Windows navigation handler hook",
)

replace_once(
    """        HRESULT STDMETHODCALLTYPE Invoke (ICoreWebView2*, ICoreWebView2PermissionRequestedEventArgs* args) override
        {
            COREWEBVIEW2_PERMISSION_KIND permissionKind;
            args->get_PermissionKind (std::addressof (permissionKind));

            if (permissionKind == COREWEBVIEW2_PERMISSION_KIND_CLIPBOARD_READ)
                args->put_State (COREWEBVIEW2_PERMISSION_STATE_ALLOW);

            return S_OK;
        }""",
    """        HRESULT STDMETHODCALLTYPE Invoke (ICoreWebView2*, ICoreWebView2PermissionRequestedEventArgs* args) override
        {
           #if defined (CHOC_WEBVIEW_WINDOWS_HANDLE_PERMISSION)
            return CHOC_WEBVIEW_WINDOWS_HANDLE_PERMISSION (args);
           #else
            COREWEBVIEW2_PERMISSION_KIND permissionKind;
            args->get_PermissionKind (std::addressof (permissionKind));
            if (permissionKind == COREWEBVIEW2_PERMISSION_KIND_CLIPBOARD_READ)
                args->put_State (COREWEBVIEW2_PERMISSION_STATE_ALLOW);
            return S_OK;
           #endif
        }""",
    "Windows permission policy hook",
)

HEADER.write_text(text, encoding="utf-8")
print(f"Patched {HEADER} ({len(text)} bytes)")
