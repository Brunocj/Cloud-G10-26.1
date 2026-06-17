import React from "react";

/**
 * AzureVm - Azure-style Virtual Machine Icon (3D Isometric Blue Cube with monitor)
 */
export const AzureVm = ({ size = 32, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
        <defs>
            <linearGradient id="vmTopGrad" x1="16" y1="2" x2="16" y2="10" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#8cc7ff" />
                <stop offset="100%" stopColor="#0078d4" />
            </linearGradient>
            <linearGradient id="vmLeftGrad" x1="4" y1="10" x2="16" y2="24" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#0078d4" />
                <stop offset="100%" stopColor="#005a9e" />
            </linearGradient>
            <linearGradient id="vmRightGrad" x1="16" y1="10" x2="28" y2="24" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#005a9e" />
                <stop offset="100%" stopColor="#004578" />
            </linearGradient>
            <linearGradient id="screenGrad" x1="10" y1="12" x2="22" y2="24" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#e6f2ff" />
                <stop offset="100%" stopColor="#aae0ff" />
            </linearGradient>
            <filter id="azureGlow" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="1.5" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
        </defs>
        
        {/* 3D Cube Base */}
        {/* Top Face */}
        <path d="M16 2L28 9L16 16L4 9L16 2Z" fill="url(#vmTopGrad)" />
        {/* Left Face */}
        <path d="M4 9V23L16 30V16L4 9Z" fill="url(#vmLeftGrad)" />
        {/* Right Face */}
        <path d="M16 16V30L28 23V9L16 16Z" fill="url(#vmRightGrad)" />

        {/* Glossy top highlight */}
        <path d="M16 3L26 8.8L16 14.6L6 8.8L16 3Z" fill="#ffffff" opacity="0.25" />

        {/* Floating Monitor Screen in center */}
        <g filter="url(#azureGlow)">
            {/* Monitor Bezel */}
            <rect x="9.5" y="10.5" width="13" height="9" rx="1.5" fill="#201f1e" stroke="#8a8886" strokeWidth="0.5" />
            {/* Display screen */}
            <rect x="10.5" y="11.5" width="11" height="7" rx="0.5" fill="url(#screenGrad)" />
            {/* CLI Console lines on screen */}
            <rect x="12" y="13" width="8" height="1" rx="0.2" fill="#0078d4" />
            <rect x="12" y="15" width="5" height="1" rx="0.2" fill="#107c41" />
            <circle cx="18.5" cy="15.5" r="0.5" fill="#d83b01" />
            {/* Stand */}
            <path d="M14 19.5L13 22H19L18 19.5H14Z" fill="#484644" />
            <rect x="13" y="21.5" width="6" height="1" fill="#323130" />
        </g>
    </svg>
);

/**
 * AzureNetwork - Network interface / connection hub
 */
export const AzureNetwork = ({ size = 32, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
        <defs>
            <linearGradient id="netGrad" x1="16" y1="2" x2="16" y2="30" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#00bcef" />
                <stop offset="100%" stopColor="#0078d4" />
            </linearGradient>
            <linearGradient id="hubGrad" x1="16" y1="11" x2="16" y2="21" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#ffffff" />
                <stop offset="100%" stopColor="#eff6fc" />
            </linearGradient>
        </defs>
        
        {/* Outer Ring */}
        <circle cx="16" cy="16" r="13" stroke="url(#netGrad)" strokeWidth="2.5" />
        <circle cx="16" cy="16" r="14.5" stroke="#0078d4" strokeWidth="0.5" opacity="0.3" />

        {/* Central Hub */}
        <circle cx="16" cy="16" r="5" fill="url(#hubGrad)" stroke="#005a9e" strokeWidth="1.5" />

        {/* Network nodes */}
        <circle cx="16" cy="6" r="3" fill="#0078d4" stroke="#ffffff" strokeWidth="1" />
        <circle cx="7" cy="21" r="3" fill="#107c41" stroke="#ffffff" strokeWidth="1" />
        <circle cx="25" cy="21" r="3" fill="#d83b01" stroke="#ffffff" strokeWidth="1" />

        {/* Connection rays */}
        <line x1="16" y1="9" x2="16" y2="11" stroke="#0078d4" strokeWidth="1.5" />
        <line x1="9.5" y1="19.5" x2="12" y2="18" stroke="#107c41" strokeWidth="1.5" />
        <line x1="22.5" y1="19.5" x2="20" y2="18" stroke="#d83b01" strokeWidth="1.5" />
    </svg>
);

/**
 * AzureDisk - Azure Storage / Managed Disk
 */
export const AzureDisk = ({ size = 32, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
        <defs>
            <linearGradient id="diskGrad" x1="16" y1="2" x2="16" y2="30" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#10c47f" />
                <stop offset="100%" stopColor="#107c41" />
            </linearGradient>
        </defs>
        {/* Cylindrical stacked disk representation */}
        {/* Shadow */}
        <ellipse cx="16" cy="25.5" rx="11" ry="4" fill="rgba(16,124,65,0.2)" />
        
        {/* Lower Plate */}
        <path d="M5 19.5C5 22 9.9 24 16 24C22.1 24 27 22 27 19.5V23.5C27 26 22.1 28 16 28C9.9 28 5 26 5 23.5V19.5Z" fill="url(#diskGrad)" stroke="#0b592e" strokeWidth="1" />
        
        {/* Middle Plate */}
        <path d="M5 11.5C5 14 9.9 16 16 16C22.1 16 27 14 27 11.5V15.5C27 18 22.1 20 16 20C9.9 20 5 18 5 15.5V11.5Z" fill="#109f62" stroke="#0b592e" strokeWidth="1" />

        {/* Top Plate */}
        <path d="M5 5.5C5 8 9.9 10 16 10C22.1 10 27 8 27 5.5V9.5C27 12 22.1 14 16 14C9.9 14 5 12 5 9.5V5.5Z" fill="#30d68f" stroke="#107c41" strokeWidth="1" />
        
        {/* Top surface */}
        <ellipse cx="16" cy="5.5" rx="11" ry="3.5" fill="#8bf7c7" stroke="#107c41" strokeWidth="1" />
        <ellipse cx="16" cy="5.5" rx="5" ry="1.5" fill="#30d68f" stroke="#107c41" opacity="0.6" />
    </svg>
);

/**
 * AzureCpu - CPU core icon (Microchip style)
 */
export const AzureCpu = ({ size = 32, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
        <defs>
            <linearGradient id="cpuGrad" x1="16" y1="4" x2="16" y2="28" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#f38b00" />
                <stop offset="100%" stopColor="#b14700" />
            </linearGradient>
        </defs>
        <rect x="6" y="6" width="20" height="20" rx="3" fill="url(#cpuGrad)" stroke="#803300" strokeWidth="1.5" />
        
        {/* Core center */}
        <rect x="10" y="10" width="12" height="12" rx="1.5" fill="#f8b155" stroke="#ffffff" strokeWidth="1" />
        <circle cx="16" cy="16" r="3" fill="#b14700" />

        {/* Pins */}
        <path d="M9 3v3 M16 3v3 M23 3v3 M9 26v3 M16 26v3 M23 26v3 M3 9h3 M3 16h3 M3 23h3 M26 9h3 M26 16h3 M26 23h3" stroke="#b14700" strokeWidth="2" strokeLinecap="round" />
    </svg>
);

/**
 * AzureRam - RAM memory icon
 */
export const AzureRam = ({ size = 32, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
        <defs>
            <linearGradient id="ramGrad" x1="16" y1="6" x2="16" y2="26" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#a060ff" />
                <stop offset="100%" stopColor="#6223e0" />
            </linearGradient>
        </defs>
        {/* Horizontal RAM module stick */}
        <rect x="3" y="10" width="26" height="12" rx="2" fill="url(#ramGrad)" stroke="#4913ad" strokeWidth="1.5" />
        
        {/* Memory chips */}
        <rect x="6" y="12" width="4" height="5" rx="0.5" fill="#201f1e" />
        <rect x="11" y="12" width="4" height="5" rx="0.5" fill="#201f1e" />
        <rect x="17" y="12" width="4" height="5" rx="0.5" fill="#201f1e" />
        <rect x="22" y="12" width="4" height="5" rx="0.5" fill="#201f1e" />

        {/* Bottom Connection Pins */}
        <path d="M5 22v1.5 M7 22v1.5 M9 22v1.5 M11 22v1.5 M13 22v1.5 M15 22v1.5 M17 22v1.5 M19 22v1.5 M21 22v1.5 M23 22v1.5 M25 22v1.5 M27 22v1.5" stroke="#f8c155" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
);

/**
 * UbuntuLogo - Colorful Ubuntu Circle logo
 */
export const UbuntuLogo = ({ size = 18, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
        <circle cx="12" cy="12" r="11" fill="#e95420" />
        <circle cx="12" cy="12" r="6" stroke="#ffffff" strokeWidth="2.5" />
        {/* Cutouts */}
        <path d="M12 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm-8.66 10a3 3 0 1 0 5.2 3 3 3 0 0 0-5.2-3zm12.12 5a3 3 0 1 0 5.2-3 3 3 0 0 0-5.2 3z" fill="#ffffff" />
        {/* Inner detail dots */}
        <circle cx="12" cy="5" r="1" fill="#e95420" />
        <circle cx="5.9" cy="15.5" r="1" fill="#e95420" />
        <circle cx="18.1" cy="15.5" r="1" fill="#e95420" />
    </svg>
);

/**
 * WindowsLogo - Colorful Windows 11 / Server Logo
 */
export const WindowsLogo = ({ size = 18, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
        <path d="M3 3h8.5v8.5H3V3zm9.5 0H21v8.5h-8.5V3zM3 12.5h8.5V21H3v-8.5zm9.5 0H21V21h-8.5v-8.5z" fill="#0078d4" />
    </svg>
);

/**
 * AzureTemplate - Azure Resource Group Template Icon
 */
export const AzureTemplate = ({ size = 28, className = "" }) => (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
        <defs>
            <linearGradient id="rgGrad" x1="16" y1="2" x2="16" y2="30" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#50e4ff" />
                <stop offset="100%" stopColor="#0078d4" />
            </linearGradient>
        </defs>
        
        {/* Layer 3 (Back) */}
        <rect x="8" y="4" width="20" height="20" rx="3" fill="#005a9e" stroke="#ffffff" strokeWidth="1" opacity="0.6" />
        
        {/* Layer 2 (Middle) */}
        <rect x="5" y="7" width="20" height="20" rx="3" fill="#0078d4" stroke="#ffffff" strokeWidth="1" opacity="0.8" />

        {/* Layer 1 (Front) */}
        <rect x="2" y="10" width="20" height="20" rx="3" fill="url(#rgGrad)" stroke="#ffffff" strokeWidth="1" />
        <circle cx="7" cy="15" r="2" fill="#ffffff" />
        <circle cx="17" cy="15" r="2" fill="#ffffff" />
        <line x1="9" y1="15" x2="15" y2="15" stroke="#ffffff" strokeWidth="1.5" />
    </svg>
);
