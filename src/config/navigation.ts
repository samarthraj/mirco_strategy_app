/**
 * Navigation Configuration
 *
 * Toggle which menu items are visible in the sidebar.
 * Set a menu to `false` to hide it from the navigation.
 */
const navigationConfig: Record<string, boolean> = {
  projects:       true,
  dashboard:      true,
  'object-browser': false,
  'data-explorer':  false,
  reports:        false,
  'api-explorer':   false,
  'request-log':    true,
  'rationalization-analysis': true,
  'cross-project': true,
};

export default navigationConfig;
