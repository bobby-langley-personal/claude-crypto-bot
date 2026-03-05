# Error Monitoring and Health Check System

This document describes the comprehensive error monitoring and health check system implemented for the crypto trading bot.

## Overview

The system consists of several components that work together to:
1. Capture and log all errors with detailed context
2. Monitor system health continuously 
3. Automatically create GitHub issues for critical problems
4. Attempt automated fixes for common errors
5. Provide manual intervention instructions when needed

## Components

### 1. Error Logger (`error_logger.py`)

Captures all exceptions with full context and stores them locally in `error_log.json`.

**Features:**
- Tracks error frequency and recurrence
- Assigns unique IDs to each error type
- Stores full stack traces and context
- Marks errors as resolved when fixed
- Tracks GitHub issue creation for errors

**Usage:**
```python
from error_logger import log_error

try:
    # Some risky operation
    pass
except Exception as e:
    error_id = log_error(e, "context description", "error", "component_name")
```

### 2. Health Checker (`health_checker.py`)

Runs comprehensive health checks and creates GitHub issues for problems.

**Checks Performed:**
- System resources (disk space, memory)
- Log file sizes
- Required files existence
- Configuration validity
- API key availability
- Portfolio file health
- Recent bot activity

**GitHub Issue Creation:**
- Automatically creates issues for critical errors
- Tags issues with "claude" label for automated fixing
- Includes detailed error information and fix instructions

### 3. Health Scheduler (`health_scheduler.py`)

Runs health checks every hour and manages the monitoring cycle.

**Features:**
- Automatic hourly health checks
- Error log cleanup (removes old resolved errors)
- Non-blocking operation (won't interfere with trading)

### 4. Auto Fixer (`auto_fixer.py`)

Attempts to automatically fix common errors by creating fix branches.

**Supported Fix Types:**
- Missing import statements
- Undefined variable references
- Simple syntax errors
- Missing file creation

**Process:**
1. Analyze error patterns
2. Determine if fix is possible with high confidence
3. Create new branch (`autofix/error-id-timestamp`)
4. Apply fix and commit
5. Mark error as fix attempted

## API Endpoints

### Health Status
- `GET /health` - Current system health status
- `GET /errors` - Error summary and recent errors  
- `POST /errors/{error_id}/resolve` - Mark an error as resolved

## Configuration

### Environment Variables

```bash
# GitHub integration
GITHUB_TOKEN=your_github_token_here
GITHUB_REPO=bobby-langley-personal/claude-crypto-bot

# Auto-fixing
AUTO_FIX_ENABLED=true
```

### File Locations

- `error_log.json` - Local error storage
- `health_check.json` - Health check history
- `bot.log` - Main application log

## Manual Intervention Process

When the system cannot automatically fix an error:

1. **GitHub Issue Created**: A detailed issue is automatically created with "claude" label
2. **Error Analysis**: Review the error details, stack trace, and context
3. **Fix Development**: Create a fix branch and implement solution
4. **Testing**: Thoroughly test the fix
5. **Resolution**: Mark the error as resolved via API or directly in code

## Automated GitHub Issue Creation

Issues are created automatically for:
- Critical errors (severity: "critical")
- High-frequency errors (same error >10 times)
- System health failures

Issue format includes:
- Error ID and technical details
- Full stack trace and context
- Step-by-step instructions for Claude Code
- Manual intervention guidance when needed

## Dashboard Integration

The web dashboard displays:
- Current health status
- Recent error summary
- Error resolution interface
- System health metrics

## Best Practices

### For Developers

1. **Use Error Logging**: Always use `log_error()` for exception handling
2. **Provide Context**: Include meaningful context when logging errors
3. **Component Naming**: Use consistent component names for categorization
4. **Test Fixes**: Thoroughly test any manual fixes before deployment

### For Operations

1. **Monitor Health**: Check `/health` endpoint regularly
2. **Review Issues**: Check GitHub issues with "claude" label daily
3. **Error Trends**: Monitor error frequency and patterns
4. **System Resources**: Keep an eye on disk space and memory usage

## Error Severity Levels

- **critical**: System-breaking errors that stop bot operation
- **error**: Significant problems that affect functionality
- **warning**: Minor issues that should be monitored

## Troubleshooting

### Common Issues

**Error logging not working:**
- Check file permissions for `error_log.json`
- Verify imports are correct in affected components

**GitHub issues not created:**
- Verify `GITHUB_TOKEN` is set correctly
- Check GitHub API rate limits
- Ensure repository permissions allow issue creation

**Health checks failing:**
- Check system resources (disk space, memory)
- Verify all required files exist
- Review configuration validity

**Auto-fixes not applying:**
- Set `AUTO_FIX_ENABLED=true`
- Check git configuration and permissions
- Review error types (only certain types are auto-fixable)

## Future Enhancements

Potential improvements to consider:
- Cloud storage integration for error logs
- Machine learning for error pattern recognition
- Integration with external monitoring services
- Advanced auto-fix capabilities
- Error prediction based on system metrics

## Support

For issues with the error monitoring system:
1. Check the health status via `/health` endpoint
2. Review recent errors via `/errors` endpoint
3. Create a GitHub issue if problems persist
4. Include system health data and error logs in reports