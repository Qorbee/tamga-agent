// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.37;

import {Ownable, Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {SignatureChecker} from "@openzeppelin/contracts/utils/cryptography/SignatureChecker.sol";

/// @title PolicyVault
/// @notice Holds each company's USDC for payouts and pays only through the executor, within that
/// company's caps and payee list, once per payment key. The limits hold whatever the backend does.
/// @dev Three roles. The admin (Ownable's `owner`) registers companies and names the executor: it
/// decides who may pay, never to whom or how much, and cannot change a registered company. The
/// executor releases and anchors. A company's own owner, always `companyOwner` here, signs that
/// company's changes.
contract PolicyVault is EIP712, Ownable2Step {
    using SafeERC20 for IERC20;

    struct Caps {
        uint256 perPayment;
        uint256 daily;
    }

    struct Company {
        address companyOwner;
        // Waits out the timelock, then replaces companyOwner without a transaction.
        address nextOwner;
        uint256 nextOwnerFrom;
        // How long a loosening waits. Fixed at registration: a timelock that can be shortened is none.
        uint256 timelock;
        uint256 nonce;
        uint256 balance;
        Caps caps;
        // Higher caps that wait out the timelock; zero `raisedFrom` means none.
        Caps raised;
        uint256 raisedFrom;
    }

    /// @notice What `companyState` returns: the values in force now, and what waits.
    struct State {
        address companyOwner;
        uint256 timelock;
        uint256 nonce;
        uint256 balance;
        uint256 perPaymentCap;
        uint256 dailyCap;
        uint256 leftToday;
        uint256 raisedPerPaymentCap;
        uint256 raisedDailyCap;
        uint256 raisedFrom;
        address nextOwner;
        uint256 nextOwnerFrom;
    }

    bytes32 public constant SET_PAYEE_TYPEHASH =
        keccak256("SetPayee(bytes32 company,address payee,bool allowed,uint256 nonce)");
    bytes32 public constant SET_CAPS_TYPEHASH =
        keccak256("SetCaps(bytes32 company,uint256 perPaymentCap,uint256 dailyCap,uint256 nonce)");
    bytes32 public constant SET_COMPANY_OWNER_TYPEHASH =
        keccak256("SetCompanyOwner(bytes32 company,address newOwner,uint256 nonce)");

    /// @notice No vault may let a loosening take effect sooner than this, however it is deployed.
    uint256 public constant TIMELOCK_FLOOR = 60;

    IERC20 public immutable usdc;
    /// @notice The shortest timelock a company may be registered with; at least the floor.
    uint256 public immutable minTimelock;

    address public executor;

    /// @notice The time from which a payee may be paid by a company; zero when not listed.
    mapping(bytes32 company => mapping(address payee => uint256)) public payeeActiveFrom;
    mapping(bytes32 company => mapping(bytes32 paymentKey => bool)) public isPaid;

    uint256 public anchorCount;
    bytes32 public lastAnchor;

    mapping(bytes32 company => Company) private _companies;
    mapping(bytes32 company => mapping(uint256 day => uint256)) private _spentOn;

    event CompanyRegistered(
        bytes32 indexed company, address indexed companyOwner, uint256 timelock, uint256 perPaymentCap, uint256 dailyCap
    );
    event Deposited(bytes32 indexed company, address indexed from, uint256 amount);
    event Released(bytes32 indexed company, bytes32 indexed paymentKey, address indexed payee, uint256 amount);
    event PayeeSet(bytes32 indexed company, address indexed payee, bool allowed, uint256 activeFrom);
    /// @dev `raisedFrom` is zero when nothing waits.
    event CapsSet(
        bytes32 indexed company,
        uint256 perPaymentCap,
        uint256 dailyCap,
        uint256 raisedPerPaymentCap,
        uint256 raisedDailyCap,
        uint256 raisedFrom
    );
    event CompanyOwnerSet(bytes32 indexed company, address indexed newOwner, uint256 activeFrom);
    event ExecutorSet(address indexed executor);
    event Anchored(uint256 indexed index, bytes32 root);

    error NotExecutor(address caller);
    error UnknownCompany(bytes32 company);
    error AlreadyRegistered(bytes32 company);
    error TimelockTooShort(uint256 timelock, uint256 shortest);
    error ZeroAddress();
    error ZeroAmount();
    error AlreadyPaid(bytes32 company, bytes32 paymentKey);
    error PayeeNotActive(bytes32 company, address payee);
    error OverPerPaymentCap(uint256 amount, uint256 cap);
    error OverDailyCap(uint256 amount, uint256 leftToday);
    error OverBalance(uint256 amount, uint256 balance);
    error BadNonce(uint256 given, uint256 expected);
    error BadSignature();
    error AdminCannotBeRenounced();

    /// @param executor_ May be zero: the vault then pays nobody until the admin names one.
    // forge-lint: disable-next-line(missing-zero-check)
    constructor(IERC20 usdc_, address admin, address executor_, uint256 minTimelock_)
        EIP712("PolicyVault", "1")
        Ownable(admin)
    {
        if (address(usdc_) == address(0)) revert ZeroAddress();
        if (minTimelock_ < TIMELOCK_FLOOR) revert TimelockTooShort(minTimelock_, TIMELOCK_FLOOR);
        usdc = usdc_;
        minTimelock = minTimelock_;
        executor = executor_;
        emit ExecutorSet(executor_);
    }

    modifier onlyExecutor() {
        if (msg.sender != executor) revert NotExecutor(msg.sender);
        _;
    }

    // ---- The admin ----

    /// @notice Registers a company once. Its first caps and payees apply at once: nothing has been
    /// deposited for it yet. Afterwards only the company's own owner can change them.
    function registerCompany(
        bytes32 company,
        address companyOwner,
        uint256 timelock,
        uint256 perPaymentCap,
        uint256 dailyCap,
        address[] calldata payees
    ) external onlyOwner {
        if (companyOwner == address(0)) revert ZeroAddress();
        Company storage c = _companies[company];
        if (c.companyOwner != address(0)) revert AlreadyRegistered(company);
        if (timelock < minTimelock) revert TimelockTooShort(timelock, minTimelock);

        c.companyOwner = companyOwner;
        c.timelock = timelock;
        c.caps = Caps(perPaymentCap, dailyCap);
        emit CompanyRegistered(company, companyOwner, timelock, perPaymentCap, dailyCap);

        for (uint256 i = 0; i < payees.length; ++i) {
            payeeActiveFrom[company][payees[i]] = block.timestamp;
            emit PayeeSet(company, payees[i], true, block.timestamp);
        }
    }

    /// @notice Names the wallet that may release and anchor. Whoever it is pays only listed payees,
    /// within each company's caps. Zero stops every payment.
    // forge-lint: disable-next-line(missing-zero-check)
    function setExecutor(address executor_) external onlyOwner {
        executor = executor_;
        emit ExecutorSet(executor_);
    }

    /// @dev Without an admin no company could be registered and a lost executor never replaced.
    function renounceOwnership() public pure override {
        revert AdminCannotBeRenounced();
    }

    // ---- Money ----

    /// @notice Credits `amount` of the caller's USDC to a company. USDC sent to this contract any
    /// other way belongs to no company and cannot be released.
    function deposit(bytes32 company, uint256 amount) external {
        Company storage c = _company(company);
        if (amount == 0) revert ZeroAmount();
        c.balance += amount;
        emit Deposited(company, msg.sender, amount);
        usdc.safeTransferFrom(msg.sender, address(this), amount);
    }

    /// @notice Pays `amount` from a company's balance to one of its payees. The only way USDC leaves.
    function release(bytes32 company, bytes32 paymentKey, address payee, uint256 amount) external onlyExecutor {
        Company storage c = _company(company);
        if (amount == 0) revert ZeroAmount();
        if (isPaid[company][paymentKey]) revert AlreadyPaid(company, paymentKey);

        uint256 activeFrom = payeeActiveFrom[company][payee];
        if (activeFrom == 0 || block.timestamp < activeFrom) revert PayeeNotActive(company, payee);

        Caps memory caps = _capsInForce(c);
        if (amount > caps.perPayment) revert OverPerPaymentCap(amount, caps.perPayment);
        uint256 day = block.timestamp / 1 days;
        uint256 spent = _spentOn[company][day];
        uint256 left = caps.daily > spent ? caps.daily - spent : 0;
        if (amount > left) revert OverDailyCap(amount, left);
        if (amount > c.balance) revert OverBalance(amount, c.balance);

        // Everything this payment changes is written before USDC is called.
        isPaid[company][paymentKey] = true;
        _spentOn[company][day] = spent + amount;
        c.balance -= amount;
        emit Released(company, paymentKey, payee, amount);
        usdc.safeTransfer(payee, amount);
    }

    /// @notice Records the root over every company's decision-log head.
    function anchor(bytes32 root) external onlyExecutor {
        uint256 index = ++anchorCount;
        lastAnchor = root;
        emit Anchored(index, root);
    }

    // ---- A company's changes, signed by its owner; anyone may submit one ----

    // The one call out before these events is the signature check, a static call: it can neither
    // write nor log, so it cannot reorder them.
    // forge-lint: disable-start(reentrancy-events)

    /// @notice Lists or unlists a payee. A removal applies at once; an addition after the timelock.
    function setPayee(bytes32 company, address payee, bool allowed, uint256 nonce, bytes calldata signature) external {
        Company storage c = _company(company);
        _settle(c);
        _useSignature(c, nonce, keccak256(abi.encode(SET_PAYEE_TYPEHASH, company, payee, allowed, nonce)), signature);

        uint256 activeFrom = 0;
        if (allowed) {
            activeFrom = payeeActiveFrom[company][payee];
            // Listing a payee again never pushes back a wait already running.
            if (activeFrom == 0) {
                activeFrom = block.timestamp + c.timelock;
                payeeActiveFrom[company][payee] = activeFrom;
            }
        } else {
            delete payeeActiveFrom[company][payee];
        }
        emit PayeeSet(company, payee, allowed, activeFrom);
    }

    /// @notice Sets both caps. Each one is held to the lower of its old and new value at once; the
    /// values asked for take over after the timelock when either is higher. Any raise that was
    /// waiting is replaced, so lowering a cap also withdraws a raise.
    function setCaps(bytes32 company, uint256 perPaymentCap, uint256 dailyCap, uint256 nonce, bytes calldata signature)
        external
    {
        Company storage c = _company(company);
        _settle(c);
        _useSignature(
            c, nonce, keccak256(abi.encode(SET_CAPS_TYPEHASH, company, perPaymentCap, dailyCap, nonce)), signature
        );

        Caps memory old = c.caps;
        c.caps = Caps(_min(old.perPayment, perPaymentCap), _min(old.daily, dailyCap));
        if (perPaymentCap > old.perPayment || dailyCap > old.daily) {
            c.raised = Caps(perPaymentCap, dailyCap);
            c.raisedFrom = block.timestamp + c.timelock;
        } else {
            delete c.raised;
            c.raisedFrom = 0;
        }
        emit CapsSet(company, c.caps.perPayment, c.caps.daily, c.raised.perPayment, c.raised.daily, c.raisedFrom);
    }

    /// @notice Names the company's next owner, who takes over after the timelock. Naming the owner
    /// in force withdraws a hand-over that is still waiting.
    function setCompanyOwner(bytes32 company, address newOwner, uint256 nonce, bytes calldata signature) external {
        if (newOwner == address(0)) revert ZeroAddress();
        Company storage c = _company(company);
        _settle(c);
        _useSignature(c, nonce, keccak256(abi.encode(SET_COMPANY_OWNER_TYPEHASH, company, newOwner, nonce)), signature);

        if (newOwner == c.companyOwner) {
            c.nextOwner = address(0);
            c.nextOwnerFrom = 0;
            emit CompanyOwnerSet(company, newOwner, block.timestamp);
        } else {
            c.nextOwner = newOwner;
            c.nextOwnerFrom = block.timestamp + c.timelock;
            emit CompanyOwnerSet(company, newOwner, c.nextOwnerFrom);
        }
    }

    // forge-lint: disable-end(reentrancy-events)

    // ---- Reading ----

    /// @notice A company's state as it is in force now, and what waits.
    function companyState(bytes32 company) external view returns (State memory) {
        Company storage c = _company(company);
        Caps memory caps = _capsInForce(c);
        uint256 spent = _spentOn[company][block.timestamp / 1 days];
        bool raiseWaits = c.raisedFrom != 0 && block.timestamp < c.raisedFrom;
        bool ownerWaits = c.nextOwnerFrom != 0 && block.timestamp < c.nextOwnerFrom;
        return State({
            companyOwner: _ownerInForce(c),
            timelock: c.timelock,
            nonce: c.nonce,
            balance: c.balance,
            perPaymentCap: caps.perPayment,
            dailyCap: caps.daily,
            leftToday: caps.daily > spent ? caps.daily - spent : 0,
            raisedPerPaymentCap: raiseWaits ? c.raised.perPayment : 0,
            raisedDailyCap: raiseWaits ? c.raised.daily : 0,
            raisedFrom: raiseWaits ? c.raisedFrom : 0,
            nextOwner: ownerWaits ? c.nextOwner : address(0),
            nextOwnerFrom: ownerWaits ? c.nextOwnerFrom : 0
        });
    }

    // ---- Internal ----

    function _company(bytes32 company) private view returns (Company storage c) {
        c = _companies[company];
        if (c.companyOwner == address(0)) revert UnknownCompany(company);
    }

    function _capsInForce(Company storage c) private view returns (Caps memory) {
        if (c.raisedFrom != 0 && block.timestamp >= c.raisedFrom) return c.raised;
        return c.caps;
    }

    function _ownerInForce(Company storage c) private view returns (address) {
        if (c.nextOwnerFrom != 0 && block.timestamp >= c.nextOwnerFrom) return c.nextOwner;
        return c.companyOwner;
    }

    /// @dev Writes down what has come due, so a change is always made against the state in force.
    function _settle(Company storage c) private {
        if (c.raisedFrom != 0 && block.timestamp >= c.raisedFrom) {
            c.caps = c.raised;
            delete c.raised;
            c.raisedFrom = 0;
        }
        if (c.nextOwnerFrom != 0 && block.timestamp >= c.nextOwnerFrom) {
            c.companyOwner = c.nextOwner;
            c.nextOwner = address(0);
            c.nextOwnerFrom = 0;
        }
    }

    /// @dev The typed data names this contract and chain (the EIP-712 domain) and the company, and
    /// the nonce is used once, so a signature is good for one change of one company here.
    function _useSignature(Company storage c, uint256 nonce, bytes32 structHash, bytes calldata signature) private {
        if (nonce != c.nonce) revert BadNonce(nonce, c.nonce);
        if (!SignatureChecker.isValidSignatureNow(c.companyOwner, _hashTypedDataV4(structHash), signature)) {
            revert BadSignature();
        }
        c.nonce = nonce + 1;
    }

    function _min(uint256 a, uint256 b) private pure returns (uint256) {
        return a < b ? a : b;
    }
}
