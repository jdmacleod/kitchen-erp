import { Navigate, Route, Routes } from "react-router";
import { AuthBridge } from "./auth/AuthBridge";
import { RequireAdmin } from "./auth/RequireAdmin";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { IngredientDetailPage } from "./pages/catalog/IngredientDetailPage";
import { IngredientsPage } from "./pages/catalog/IngredientsPage";
import { ProductDetailPage } from "./pages/catalog/ProductDetailPage";
import { ProductsPage } from "./pages/catalog/ProductsPage";
import { MapPage } from "./pages/geo/MapPage";
import { VendorDetailPage } from "./pages/geo/VendorDetailPage";
import { VendorsPage } from "./pages/geo/VendorsPage";
import { ComparePage } from "./pages/pricebook/ComparePage";
import { NeedsBridgePage } from "./pages/pricebook/NeedsBridgePage";
import { NewPurchasePage } from "./pages/purchases/NewPurchasePage";
import { PurchaseDetailPage } from "./pages/purchases/PurchaseDetailPage";
import { PurchasesPage } from "./pages/purchases/PurchasesPage";
import { ReceiptsPage } from "./pages/purchases/ReceiptsPage";
import { ShelfPricePage } from "./pages/purchases/ShelfPricePage";
import { ToIdentifyPage } from "./pages/purchases/ToIdentifyPage";
import { HomeBasesPage } from "./pages/settings/HomeBasesPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { TokensPage } from "./pages/settings/TokensPage";
import { UsersPage } from "./pages/settings/UsersPage";

/** Route table. Mounted inside a router and a QueryClientProvider by main.tsx and the tests. */
export function App() {
  return (
    <>
      <AuthBridge />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route index element={<Navigate to="/ingredients" replace />} />
            {/* Phase 1C: ingredients and products */}
            <Route path="/ingredients" element={<IngredientsPage />} />
            <Route path="/ingredients/:id" element={<IngredientDetailPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/products/:id" element={<ProductDetailPage />} />
            {/* Phase 1D/1E: vendors, home bases, and the map */}
            <Route path="/vendors" element={<VendorsPage />} />
            <Route path="/vendors/:id" element={<VendorDetailPage />} />
            <Route path="/map" element={<MapPage />} />
            <Route path="/settings/home-bases" element={<HomeBasesPage />} />
            {/* Phase 2A/2B: shelf prices and manual purchases */}
            <Route path="/purchases" element={<PurchasesPage />} />
            <Route path="/purchases/new" element={<NewPurchasePage />} />
            <Route path="/purchases/:id" element={<PurchaseDetailPage />} />
            <Route path="/prices/new" element={<ShelfPricePage />} />
            {/* Phase 2C/2D: receipt ingest, review, and the to-identify queue */}
            <Route path="/receipts" element={<ReceiptsPage />} />
            <Route path="/to-identify" element={<ToIdentifyPage />} />
            {/* Phase 2E: price book views */}
            <Route path="/compare" element={<ComparePage />} />
            <Route path="/price-book/needs-bridge" element={<NeedsBridgePage />} />
            <Route
              path="/settings/users"
              element={
                <RequireAdmin>
                  <UsersPage />
                </RequireAdmin>
              }
            />
            <Route path="/settings/tokens" element={<TokensPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </>
  );
}
