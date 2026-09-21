import { Navigate, Route, Routes } from "react-router";
import { AuthBridge } from "./auth/AuthBridge";
import { RequireAdmin } from "./auth/RequireAdmin";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { CatalogPage, catalogPages } from "./pages/CatalogPage";
import { IngredientDetailPage } from "./pages/catalog/IngredientDetailPage";
import { IngredientsPage } from "./pages/catalog/IngredientsPage";
import { ProductDetailPage } from "./pages/catalog/ProductDetailPage";
import { ProductsPage } from "./pages/catalog/ProductsPage";
import { MapPage } from "./pages/geo/MapPage";
import { VendorDetailPage } from "./pages/geo/VendorDetailPage";
import { VendorsPage } from "./pages/geo/VendorsPage";
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
            <Route path="/purchases" element={<CatalogPage {...catalogPages.purchases} />} />
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
