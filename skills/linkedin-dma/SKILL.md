---
name: linkedin-dma
description: Access LinkedIn Member Data Portability (DMA) APIs to fetch the authenticated member's own data — profile, posts, articles, and recent changelog events. Geographically restricted; see the skill for access requirements.
---

# LinkedIn DMA MCP Server

## Purpose
This GRIP MCP server provides tools to access LinkedIn Member Data Portability (DMA) APIs, allowing authenticated LinkedIn members to fetch their own data, including historical data (profile, posts, articles) and recent interaction events (changelog).

## Important Limitation: Geographical Restriction
**This API is currently available only for LinkedIn members located in the European Economic Area (EEA) and Switzerland.** Only members within these regions can consent and generate an access token. This limits the universal applicability of this tool.

## Data Available
- **Member Snapshot API**: Historical member information (profile, posts, articles, connections).
- **Member Changelog API**: Interaction events (posts, comments, reactions) from the past 28 days.

## Getting Access
1.  **Create a Developer Application** on the [LinkedIn Developers Platform](https://developer.linkedin.com/apps/). Use the provided default LinkedIn Company Page specifically for requesting access to the Member Data Portability (Member) APIs.
2.  **Request access to API product** for "Member Data Portability API (Member)" from the Products tab of your application.

## Getting an Access Token
Members can generate an access token via the [OAuth Token Generator Tool](https://www.linkedin.com/developers/tools/oauth) in the Developer Portal.
1.  Select the application provisioned with the "Member Data Portability API (Member)" product.
2.  Select `"r_dma_portability_self_serve"` as the scope.
3.  Login and provide consent to share your LinkedIn Data with your application.
4.  The generated access token can be used for authenticated API calls.

## MCP Server Tools

The `linkedin-dma` MCP server provides the following tools:

-   `linkedin_get_snapshot(domain: Optional[str] = None, start: int = 0, count: int = 10)`: Fetch member snapshot data.
-   `linkedin_get_changelog(start_time: Optional[int] = None, count: int = 10)`: Fetch member changelog events (past 28 days).
-   `linkedin_check_authorization()`: Check if changelog events are being archived.
-   `linkedin_enable_changelog()`: Manually enable changelog event generation.

## Configuration (Org-Specific)
This MCP server is intended for use within organisations operating in EEA/Switzerland due to its geographical restrictions. It relies on the `LINKEDIN_ACCESS_TOKEN` environment variable or the macOS Keychain for authentication.

To use:
1. Set the `LINKEDIN_ACCESS_TOKEN` environment variable with your OAuth token, or configure it in your macOS Keychain under the service name `grip-linkedin-token`.
2. Ensure the `linkedin-dma` server is enabled in your project-specific or user-scoped `.mcp.json` configuration.

---
