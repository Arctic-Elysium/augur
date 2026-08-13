import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { SessionProvider } from "./lib/session";
import { Shell } from "./routes/Shell";
import { CampaignList } from "./routes/CampaignList";
import { CampaignDetail } from "./routes/CampaignDetail";
import { Play } from "./routes/Play";
import "./styles/tokens.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<CampaignList />} />
            <Route path="campaigns/:campaignId" element={<CampaignDetail />} />
            <Route path="play/:sessionId" element={<Play />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  </StrictMode>,
);
