import { motion } from "framer-motion";
import { Compass } from "lucide-react";
import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} className="card p-8 text-center max-w-md w-full">
        <div className="mx-auto h-14 w-14 rounded-2xl bg-indigo-500/10 text-indigo-500 flex items-center justify-center mb-4">
          <Compass className="h-7 w-7" />
        </div>
        <p className="text-5xl font-semibold gradient-text tabular-nums">404</p>
        <h1 className="text-lg font-semibold mt-2">Page not found</h1>
        <p className="text-sm muted mt-1">The page you are looking for does not exist or was moved.</p>
        <Link to="/" className="btn-primary mt-5 inline-flex">
          Back to overview
        </Link>
      </motion.div>
    </div>
  );
}
