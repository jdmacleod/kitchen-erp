import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { createQueryClient } from "./lib/queryClient";
// Self-hosted fonts: the app makes no runtime requests to a font CDN.
// Fraunces needs its "full" build for the opsz and SOFT axes theme.css sets on h1.
import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/inter/index.css";
import "./index.css";

const queryClient = createQueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
