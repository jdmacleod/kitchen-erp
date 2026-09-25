import { Route, Routes } from "react-router";
import { AuthBridge } from "./auth/AuthBridge";
import { RequireAdmin } from "./auth/RequireAdmin";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { RedirectTo } from "./components/RedirectTo";
import { IngredientDetailPage } from "./pages/catalog/IngredientDetailPage";
import { IngredientsPage } from "./pages/catalog/IngredientsPage";
import { ProductDetailPage } from "./pages/catalog/ProductDetailPage";
import { ProductsPage } from "./pages/catalog/ProductsPage";
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
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SystemPage } from "./pages/settings/SystemPage";
import { TokensPage } from "./pages/settings/TokensPage";
import { UsersPage } from "./pages/settings/UsersPage";

/** Old paths and where they now live (docs/spec/09, Routes); parameters carry over. */
export const REDIRECTS: { from: string; to: string; query?: Record<string, string> }[] = [
  { from: "/purchases", to: "/shop/purchases" },
  { from: "/purchases/new", to: "/shop/purchases/new" },
  { from: "/purchases/:id", to: "/shop/purchases/:id" },
  { from: "/receipts", to: "/shop/receipts" },
  { from: "/to-identify", to: "/shop/receipts/identify" },
  { from: "/prices/new", to: "/shop/shelf-prices" },
  { from: "/compare", to: "/shop/compare" },
  { from: "/ingredients", to: "/catalog/ingredients" },
  { from: "/ingredients/:id", to: "/catalog/ingredients/:id" },
  { from: "/products", to: "/catalog/products" },
  { from: "/products/:id", to: "/catalog/products/:id" },
  { from: "/vendors", to: "/catalog/vendors" },
  { from: "/vendors/:id", to: "/catalog/vendors/:id" },
  { from: "/map", to: "/catalog/vendors", query: { view: "map" } },
  { from: "/price-book/needs-bridge", to: "/catalog/bridges" },
  { from: "/settings/home-bases", to: "/settings/kitchens" },
];

/**
 * Route table (docs/spec/09-information-architecture.md, Routes). Mounted inside a
 * router and a QueryClientProvider by main.tsx and the tests.
 */
export function App() {
  return (
    <>
      <AuthBridge />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route index element={<HomePage />} />

            {/* Shop */}
            <Route path="/shop/purchases" element={<PurchasesPage />} />
            <Route path="/shop/purchases/new" element={<NewPurchasePage />} />
            <Route path="/shop/purchases/:id" element={<PurchaseDetailPage />} />
            <Route path="/shop/receipts" element={<ReceiptsPage />} />
            <Route path="/shop/receipts/identify" element={<ToIdentifyPage />} />
            <Route path="/shop/shelf-prices" element={<ShelfPricePage />} />
            <Route path="/shop/compare" element={<ComparePage />} />

            {/* Catalog */}
            <Route path="/catalog/ingredients" element={<IngredientsPage />} />
            <Route path="/catalog/ingredients/:id" element={<IngredientDetailPage />} />
            <Route path="/catalog/products" element={<ProductsPage />} />
            <Route path="/catalog/products/:id" element={<ProductDetailPage />} />
            <Route path="/catalog/vendors" element={<VendorsPage />} />
            <Route path="/catalog/vendors/:id" element={<VendorDetailPage />} />
            <Route path="/catalog/bridges" element={<NeedsBridgePage />} />

            {/* Settings */}
            <Route path="/settings/kitchens" element={<HomeBasesPage />} />
            <Route
              path="/settings/users"
              element={
                <RequireAdmin>
                  <UsersPage />
                </RequireAdmin>
              }
            />
            <Route path="/settings/tokens" element={<TokensPage />} />
            <Route path="/settings/system" element={<SystemPage />} />

            {/* Retired paths, kept as redirects for at least one release. */}
            {REDIRECTS.map(({ from, to, query }) => (
              <Route key={from} path={from} element={<RedirectTo to={to} query={query} />} />
            ))}

            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </>
  );
}
