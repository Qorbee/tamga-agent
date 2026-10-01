// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.36;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {PolicyVault} from "../src/PolicyVault.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";
import {ReentrantToken} from "./mocks/ReentrantToken.sol";
import {SmartAccount} from "./mocks/SmartAccount.sol";

contract PolicyVaultTest is Test {
    uint256 constant USDC = 1e6;
    uint256 constant MIN_TIMELOCK = 60;

    // Company A is shaped like the pilot, B like a sandbox.
    bytes32 constant A = bytes32(uint256(0xA));
    bytes32 constant B = bytes32(uint256(0xB));
    uint256 constant A_TIMELOCK = 1 days;
    uint256 constant B_TIMELOCK = 120;
    uint256 constant A_PER_PAYMENT = 500 * USDC;
    uint256 constant A_DAILY = 1_000 * USDC;
    uint256 constant B_PER_PAYMENT = 50 * USDC;
    uint256 constant B_DAILY = 100 * USDC;

    MockUSDC usdc;
    PolicyVault vault;

    address admin = makeAddr("admin");
    address executor = makeAddr("executor");
    address treasury = makeAddr("treasury");
    address carrierA = makeAddr("carrierA");
    address carrierB = makeAddr("carrierB");
    address stranger = makeAddr("stranger");
    address ownerA;
    uint256 ownerAKey;
    address ownerB;
    uint256 ownerBKey;

    event Released(bytes32 indexed company, bytes32 indexed paymentKey, address indexed payee, uint256 amount);
    event PayeeSet(bytes32 indexed company, address indexed payee, bool allowed, uint256 activeFrom);
    event ExecutorSet(address indexed executor);
    event Anchored(uint256 indexed index, bytes32 root);

    function setUp() public {
        // Not the chain's first second: a zero timestamp would read as "not listed".
        vm.warp(1_790_000_000);
        (ownerA, ownerAKey) = makeAddrAndKey("ownerA");
        (ownerB, ownerBKey) = makeAddrAndKey("ownerB");

        usdc = new MockUSDC();
        vault = new PolicyVault(IERC20(address(usdc)), admin, executor, MIN_TIMELOCK);

        vm.startPrank(admin);
        vault.registerCompany(A, ownerA, A_TIMELOCK, A_PER_PAYMENT, A_DAILY, _one(carrierA));
        vault.registerCompany(B, ownerB, B_TIMELOCK, B_PER_PAYMENT, B_DAILY, _one(carrierB));
        vm.stopPrank();

        usdc.mint(treasury, 100_000 * USDC);
        vm.prank(treasury);
        usdc.approve(address(vault), type(uint256).max);
        _deposit(A, 5_000 * USDC);
        _deposit(B, 200 * USDC);
    }

    // ---- release ----

    function test_Release_PaysAListedPayeeWithinTheCaps() public {
        vm.expectEmit(address(vault));
        emit Released(A, "invoice-1", carrierA, 400 * USDC);
        _release(A, "invoice-1", carrierA, 400 * USDC);

        assertEq(usdc.balanceOf(carrierA), 400 * USDC);
        assertTrue(vault.isPaid(A, "invoice-1"));
        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.balance, 4_600 * USDC);
        assertEq(state.leftToday, 600 * USDC);
    }

    function test_Release_RevertsForAnyoneButTheExecutor() public {
        address[3] memory callers = [admin, ownerA, stranger];
        for (uint256 i = 0; i < callers.length; ++i) {
            vm.expectRevert(abi.encodeWithSelector(PolicyVault.NotExecutor.selector, callers[i]));
            vm.prank(callers[i]);
            vault.release(A, "invoice-1", carrierA, 1 * USDC);
        }
    }

    function test_Release_RevertsForACompanyNeverRegistered() public {
        bytes32 nobody = bytes32(uint256(0xC));
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.UnknownCompany.selector, nobody));
        _release(nobody, "invoice-1", carrierA, 1 * USDC);
    }

    function test_Release_RevertsOnAZeroAmount() public {
        vm.expectRevert(PolicyVault.ZeroAmount.selector);
        _release(A, "invoice-1", carrierA, 0);
    }

    function test_Release_PaysAKeyOnce() public {
        _release(A, "invoice-1", carrierA, 100 * USDC);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.AlreadyPaid.selector, A, bytes32("invoice-1")));
        _release(A, "invoice-1", carrierA, 100 * USDC);
        // Not even on another day, or for another amount.
        vm.warp(vm.getBlockTimestamp() + 3 days);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.AlreadyPaid.selector, A, bytes32("invoice-1")));
        _release(A, "invoice-1", carrierA, 1 * USDC);
        assertEq(usdc.balanceOf(carrierA), 100 * USDC);
    }

    function test_Release_RevertsForAPayeeNotListed() public {
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.PayeeNotActive.selector, A, stranger));
        _release(A, "invoice-1", stranger, 1 * USDC);
    }

    function test_Release_RevertsAboveThePerPaymentCap() public {
        vm.expectRevert(
            abi.encodeWithSelector(PolicyVault.OverPerPaymentCap.selector, A_PER_PAYMENT + 1, A_PER_PAYMENT)
        );
        _release(A, "invoice-1", carrierA, A_PER_PAYMENT + 1);
        _release(A, "invoice-1", carrierA, A_PER_PAYMENT);
    }

    function test_Release_RevertsAboveWhatIsLeftToday_AndTheCapComesBackWithTheUtcDay() public {
        // Three seconds before midnight UTC.
        vm.warp((vm.getBlockTimestamp() / 1 days + 1) * 1 days - 3);
        _release(A, "invoice-1", carrierA, 500 * USDC);
        _release(A, "invoice-2", carrierA, 400 * USDC);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverDailyCap.selector, 101 * USDC, 100 * USDC));
        _release(A, "invoice-3", carrierA, 101 * USDC);

        vm.warp(vm.getBlockTimestamp() + 2);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverDailyCap.selector, 101 * USDC, 100 * USDC));
        _release(A, "invoice-3", carrierA, 101 * USDC);

        vm.warp(vm.getBlockTimestamp() + 1);
        _release(A, "invoice-3", carrierA, 101 * USDC);
        assertEq(vault.companyState(A).leftToday, A_DAILY - 101 * USDC);
    }

    function test_Release_RevertsAboveTheCompanysBalance() public {
        // B holds 200 and may pay 50 at a time, 100 a day: four days empty it.
        for (uint256 day = 0; day < 2; ++day) {
            _release(B, bytes32(day * 2), carrierB, 50 * USDC);
            _release(B, bytes32(day * 2 + 1), carrierB, 50 * USDC);
            vm.warp(vm.getBlockTimestamp() + 1 days);
        }
        assertEq(vault.companyState(B).balance, 0);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverBalance.selector, 1 * USDC, 0));
        _release(B, "one-more", carrierB, 1 * USDC);
    }

    function test_Release_ATokenThatCallsBackCannotBePaidTwice() public {
        ReentrantToken token = new ReentrantToken();
        // The token is the executor, so only the vault's own state stands in its way.
        PolicyVault trap = new PolicyVault(IERC20(address(token)), admin, address(token), MIN_TIMELOCK);
        vm.prank(admin);
        trap.registerCompany(A, ownerA, A_TIMELOCK, A_PER_PAYMENT, A_DAILY, _one(carrierA));
        token.mint(address(this), 1_000 * USDC);
        token.approve(address(trap), 1_000 * USDC);
        trap.deposit(A, 1_000 * USDC);

        token.aim(trap, A, "invoice-1");
        token.pay(carrierA, 300 * USDC);

        assertTrue(token.reentered());
        assertEq(token.innerRevert(), abi.encodeWithSelector(PolicyVault.AlreadyPaid.selector, A, bytes32("invoice-1")));
        assertEq(token.balanceOf(carrierA), 300 * USDC);
        assertEq(trap.companyState(A).balance, 700 * USDC);
    }

    // ---- one company against another ----

    function test_Isolation_ACompanySpendsOnlyItsOwnBalance() public {
        bytes32 empty = bytes32(uint256(0xE));
        vm.prank(admin);
        vault.registerCompany(empty, ownerA, A_TIMELOCK, A_PER_PAYMENT, A_DAILY, _one(carrierA));
        // The vault holds 5,200 USDC of A's and B's; none of it is this company's.
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverBalance.selector, 1 * USDC, 0));
        _release(empty, "invoice-1", carrierA, 1 * USDC);
    }

    function test_Isolation_ACompanysPayeesAreNotAnothers() public {
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.PayeeNotActive.selector, B, carrierA));
        _release(B, "invoice-1", carrierA, 1 * USDC);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.PayeeNotActive.selector, A, carrierB));
        _release(A, "invoice-1", carrierB, 1 * USDC);
    }

    function test_Isolation_ACompanysCapsAreNotAnothers() public {
        // 60 is within A's per-payment cap and above B's.
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverPerPaymentCap.selector, 60 * USDC, B_PER_PAYMENT));
        _release(B, "invoice-1", carrierB, 60 * USDC);

        // A spending its whole day leaves B's day untouched.
        _release(A, "invoice-1", carrierA, 500 * USDC);
        _release(A, "invoice-2", carrierA, 500 * USDC);
        assertEq(vault.companyState(A).leftToday, 0);
        assertEq(vault.companyState(B).leftToday, B_DAILY);
        _release(B, "invoice-1", carrierB, 50 * USDC);
    }

    function test_Isolation_APaidKeyIsPaidForItsCompanyOnly() public {
        _release(A, "invoice-1", carrierA, 10 * USDC);
        assertFalse(vault.isPaid(B, "invoice-1"));
        _release(B, "invoice-1", carrierB, 10 * USDC);
    }

    function test_Isolation_ACompanysOwnerChangesNothingOfAnothers() public {
        // A's owner signs B's change with B's nonce: the signature is not B's owner's.
        bytes memory byA = _sign(ownerAKey, _payeeHash(B, stranger, true, 0));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(B, stranger, true, 0, byA);

        byA = _sign(ownerAKey, _capsHash(B, 1, 1, 0));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setCaps(B, 1, 1, 0, byA);

        byA = _sign(ownerAKey, _ownerHash(B, ownerA, 0));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setCompanyOwner(B, ownerA, 0, byA);

        // And a change A's owner signed for A is not accepted for B.
        bytes memory forA = _sign(ownerAKey, _payeeHash(A, stranger, true, 0));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(B, stranger, true, 0, forA);
    }

    // ---- payees ----

    function test_SetPayee_ANewPayeeWaitsOutTheTimelock() public {
        uint256 ready = vm.getBlockTimestamp() + A_TIMELOCK;
        vm.expectEmit(address(vault));
        emit PayeeSet(A, stranger, true, ready);
        _setPayee(A, ownerAKey, stranger, true);
        assertEq(vault.payeeActiveFrom(A, stranger), ready);

        vm.warp(ready - 1);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.PayeeNotActive.selector, A, stranger));
        _release(A, "invoice-1", stranger, 1 * USDC);

        vm.warp(ready);
        _release(A, "invoice-1", stranger, 1 * USDC);
        assertEq(usdc.balanceOf(stranger), 1 * USDC);
    }

    function test_SetPayee_ARemovalAppliesAtOnce() public {
        _setPayee(A, ownerAKey, carrierA, false);
        assertEq(vault.payeeActiveFrom(A, carrierA), 0);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.PayeeNotActive.selector, A, carrierA));
        _release(A, "invoice-1", carrierA, 1 * USDC);
    }

    function test_SetPayee_ARemovalWithdrawsAnAdditionStillWaiting() public {
        _setPayee(A, ownerAKey, stranger, true);
        _setPayee(A, ownerAKey, stranger, false);
        vm.warp(vm.getBlockTimestamp() + A_TIMELOCK + 1);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.PayeeNotActive.selector, A, stranger));
        _release(A, "invoice-1", stranger, 1 * USDC);
    }

    function test_SetPayee_ListingAgainNeverPushesTheWaitBack() public {
        _setPayee(A, ownerAKey, stranger, true);
        uint256 ready = vault.payeeActiveFrom(A, stranger);
        vm.warp(vm.getBlockTimestamp() + A_TIMELOCK / 2);
        _setPayee(A, ownerAKey, stranger, true);
        assertEq(vault.payeeActiveFrom(A, stranger), ready);
    }

    function test_SetPayee_EachCompanyWaitsItsOwnTimelock() public {
        _setPayee(B, ownerBKey, stranger, true);
        vm.warp(vm.getBlockTimestamp() + B_TIMELOCK);
        _release(B, "invoice-1", stranger, 1 * USDC);
    }

    // ---- caps ----

    function test_SetCaps_ALowerCapAppliesAtOnce() public {
        _setCaps(A, ownerAKey, 100 * USDC, 300 * USDC);
        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.perPaymentCap, 100 * USDC);
        assertEq(state.dailyCap, 300 * USDC);
        assertEq(state.raisedFrom, 0);

        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverPerPaymentCap.selector, 101 * USDC, 100 * USDC));
        _release(A, "invoice-1", carrierA, 101 * USDC);
    }

    function test_SetCaps_AHigherCapWaitsOutTheTimelock() public {
        uint256 ready = vm.getBlockTimestamp() + A_TIMELOCK;
        _setCaps(A, ownerAKey, 2_000 * USDC, 4_000 * USDC);

        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.perPaymentCap, A_PER_PAYMENT);
        assertEq(state.dailyCap, A_DAILY);
        assertEq(state.raisedPerPaymentCap, 2_000 * USDC);
        assertEq(state.raisedDailyCap, 4_000 * USDC);
        assertEq(state.raisedFrom, ready);

        vm.warp(ready - 1);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverPerPaymentCap.selector, 2_000 * USDC, A_PER_PAYMENT));
        _release(A, "invoice-1", carrierA, 2_000 * USDC);

        vm.warp(ready);
        _release(A, "invoice-1", carrierA, 2_000 * USDC);
        state = vault.companyState(A);
        assertEq(state.perPaymentCap, 2_000 * USDC);
        assertEq(state.dailyCap, 4_000 * USDC);
        assertEq(state.raisedFrom, 0);
    }

    function test_SetCaps_OneUpAndOneDown_TheLowerHoldsNowAndTheHigherWaits() public {
        // Per payment up from 500 to 800, daily down from 1,000 to 900.
        _setCaps(A, ownerAKey, 800 * USDC, 900 * USDC);
        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.perPaymentCap, A_PER_PAYMENT);
        assertEq(state.dailyCap, 900 * USDC);

        vm.warp(vm.getBlockTimestamp() + A_TIMELOCK);
        state = vault.companyState(A);
        assertEq(state.perPaymentCap, 800 * USDC);
        assertEq(state.dailyCap, 900 * USDC);
    }

    function test_SetCaps_LoweringWithdrawsARaiseStillWaiting() public {
        _setCaps(A, ownerAKey, 2_000 * USDC, 4_000 * USDC);
        _setCaps(A, ownerAKey, A_PER_PAYMENT, A_DAILY);
        vm.warp(vm.getBlockTimestamp() + A_TIMELOCK + 1);
        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.perPaymentCap, A_PER_PAYMENT);
        assertEq(state.dailyCap, A_DAILY);
    }

    function test_SetCaps_ARaiseThatCameDueIsTheBaseOfTheNextChange() public {
        _setCaps(A, ownerAKey, 2_000 * USDC, 4_000 * USDC);
        vm.warp(vm.getBlockTimestamp() + A_TIMELOCK);
        // Down from the raised 2,000 to 1,500: a tightening, so it holds at once.
        _setCaps(A, ownerAKey, 1_500 * USDC, 4_000 * USDC);
        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.perPaymentCap, 1_500 * USDC);
        assertEq(state.dailyCap, 4_000 * USDC);
        assertEq(state.raisedFrom, 0);
    }

    function test_SetCaps_ADailyCapLoweredBelowTodaysSpendingLeavesNothing() public {
        _release(A, "invoice-1", carrierA, 400 * USDC);
        _setCaps(A, ownerAKey, A_PER_PAYMENT, 300 * USDC);
        assertEq(vault.companyState(A).leftToday, 0);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.OverDailyCap.selector, 1, 0));
        _release(A, "invoice-2", carrierA, 1);
    }

    // ---- the company's owner ----

    function test_SetCompanyOwner_TheNewOwnerTakesOverAfterTheTimelock() public {
        (address heir, uint256 heirKey) = makeAddrAndKey("heir");
        uint256 ready = vm.getBlockTimestamp() + A_TIMELOCK;
        vault.setCompanyOwner(A, heir, 0, _sign(ownerAKey, _ownerHash(A, heir, 0)));

        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.companyOwner, ownerA);
        assertEq(state.nextOwner, heir);
        assertEq(state.nextOwnerFrom, ready);

        // Until then the heir signs nothing, and the old owner still does.
        vm.warp(ready - 1);
        bytes memory early = _sign(heirKey, _payeeHash(A, stranger, false, 1));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(A, stranger, false, 1, early);
        _setPayee(A, ownerAKey, stranger, false);

        // From then on it is the other way round.
        vm.warp(ready);
        assertEq(vault.companyState(A).companyOwner, heir);
        bytes memory late = _sign(ownerAKey, _payeeHash(A, carrierA, false, 2));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(A, carrierA, false, 2, late);
        _setPayee(A, heirKey, carrierA, false);
    }

    function test_SetCompanyOwner_NamingTheOwnerInForceWithdrawsAHandOver() public {
        address heir = makeAddr("heir");
        vault.setCompanyOwner(A, heir, 0, _sign(ownerAKey, _ownerHash(A, heir, 0)));
        vault.setCompanyOwner(A, ownerA, 1, _sign(ownerAKey, _ownerHash(A, ownerA, 1)));
        vm.warp(vm.getBlockTimestamp() + A_TIMELOCK + 1);
        PolicyVault.State memory state = vault.companyState(A);
        assertEq(state.companyOwner, ownerA);
        assertEq(state.nextOwner, address(0));
    }

    function test_SetCompanyOwner_RefusesTheZeroAddress() public {
        bytes memory signature = _sign(ownerAKey, _ownerHash(A, address(0), 0));
        vm.expectRevert(PolicyVault.ZeroAddress.selector);
        vault.setCompanyOwner(A, address(0), 0, signature);
    }

    function test_AnOwnerMayBeASmartAccount() public {
        (address signer, uint256 signerKey) = makeAddrAndKey("signer");
        SmartAccount account = new SmartAccount(signer);
        bytes32 company = bytes32(uint256(0xD));
        vm.prank(admin);
        vault.registerCompany(company, address(account), MIN_TIMELOCK, 1, 1, new address[](0));

        vault.setPayee(company, stranger, true, 0, _sign(signerKey, _payeeHash(company, stranger, true, 0)));
        assertEq(vault.payeeActiveFrom(company, stranger), vm.getBlockTimestamp() + MIN_TIMELOCK);

        bytes memory wrong = _sign(ownerAKey, _payeeHash(company, carrierA, true, 1));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(company, carrierA, true, 1, wrong);
    }

    // ---- a signature is good once, for one company, here ----

    function test_Signature_IsRefusedASecondTime() public {
        bytes memory signature = _sign(ownerAKey, _payeeHash(A, stranger, true, 0));
        vault.setPayee(A, stranger, true, 0, signature);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.BadNonce.selector, 0, 1));
        vault.setPayee(A, stranger, true, 0, signature);
    }

    function test_Signature_IsRefusedWithANonceAheadOfTheCompanys() public {
        bytes memory signature = _sign(ownerAKey, _payeeHash(A, stranger, true, 5));
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.BadNonce.selector, 5, 0));
        vault.setPayee(A, stranger, true, 5, signature);
    }

    function test_Signature_IsRefusedWhenAnyFieldDiffersFromWhatWasSigned() public {
        bytes memory signature = _sign(ownerAKey, _payeeHash(A, stranger, false, 0));
        // Signed as a removal, submitted as an addition; then for another payee.
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(A, stranger, true, 0, signature);
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(A, carrierA, false, 0, signature);
    }

    function test_Signature_IsRefusedFromAnyKeyButTheOwners() public {
        (, uint256 strangerKey) = makeAddrAndKey("stranger");
        bytes memory signature = _sign(strangerKey, _payeeHash(A, stranger, true, 0));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(A, stranger, true, 0, signature);
    }

    function test_Signature_ForAnotherVaultIsRefused() public {
        PolicyVault other = new PolicyVault(IERC20(address(usdc)), admin, executor, MIN_TIMELOCK);
        vm.prank(admin);
        other.registerCompany(A, ownerA, A_TIMELOCK, A_PER_PAYMENT, A_DAILY, _one(carrierA));

        // Signed for the first vault: same company, same owner, same nonce.
        bytes memory signature = _sign(ownerAKey, _payeeHash(A, stranger, true, 0));
        vm.expectRevert(PolicyVault.BadSignature.selector);
        other.setPayee(A, stranger, true, 0, signature);
    }

    function test_Signature_ForAnotherChainIsRefused() public {
        bytes memory signature = _sign(ownerAKey, _payeeHash(A, stranger, true, 0));
        vm.chainId(vm.getChainId() + 1);
        vm.expectRevert(PolicyVault.BadSignature.selector);
        vault.setPayee(A, stranger, true, 0, signature);
    }

    // ---- the admin ----

    function test_RegisterCompany_OnlyTheAdmin() public {
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, executor));
        vm.prank(executor);
        vault.registerCompany(bytes32(uint256(0xC)), ownerA, A_TIMELOCK, 1, 1, new address[](0));
    }

    function test_RegisterCompany_ASecondTimeReverts() public {
        // The admin cannot rewrite a company by registering it again.
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.AlreadyRegistered.selector, A));
        vm.prank(admin);
        vault.registerCompany(A, admin, MIN_TIMELOCK, type(uint256).max, type(uint256).max, _one(stranger));
        assertEq(vault.companyState(A).companyOwner, ownerA);
    }

    function test_RegisterCompany_RefusesATimelockUnderTheShortest() public {
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.TimelockTooShort.selector, MIN_TIMELOCK - 1, MIN_TIMELOCK));
        vm.prank(admin);
        vault.registerCompany(bytes32(uint256(0xC)), ownerA, MIN_TIMELOCK - 1, 1, 1, new address[](0));
    }

    function test_AVaultCannotBeDeployedWithAShortestTimelockUnderTheFloor() public {
        // Whatever a deploy script is told, no vault lets a loosening apply at once.
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.TimelockTooShort.selector, 59, 60));
        new PolicyVault(IERC20(address(usdc)), admin, executor, 59);
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.TimelockTooShort.selector, 0, 60));
        new PolicyVault(IERC20(address(usdc)), admin, executor, 0);

        PolicyVault stricter = new PolicyVault(IERC20(address(usdc)), admin, executor, 1 hours);
        assertEq(stricter.minTimelock(), 1 hours);
        assertEq(stricter.TIMELOCK_FLOOR(), 60);
    }

    function test_RegisterCompany_RefusesNoOwner() public {
        vm.expectRevert(PolicyVault.ZeroAddress.selector);
        vm.prank(admin);
        vault.registerCompany(bytes32(uint256(0xC)), address(0), A_TIMELOCK, 1, 1, new address[](0));
    }

    function test_SetExecutor_OnlyTheAdmin_AndZeroStopsEveryPayment() public {
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, executor));
        vm.prank(executor);
        vault.setExecutor(stranger);

        address fallbackWallet = makeAddr("fallback");
        vm.expectEmit(address(vault));
        emit ExecutorSet(fallbackWallet);
        vm.prank(admin);
        vault.setExecutor(fallbackWallet);

        // The old executor is out, the new one is held to the same limits.
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.NotExecutor.selector, executor));
        _release(A, "invoice-1", carrierA, 1 * USDC);
        vm.expectRevert(
            abi.encodeWithSelector(PolicyVault.OverPerPaymentCap.selector, A_PER_PAYMENT + 1, A_PER_PAYMENT)
        );
        vm.prank(fallbackWallet);
        vault.release(A, "invoice-1", carrierA, A_PER_PAYMENT + 1);

        vm.prank(admin);
        vault.setExecutor(address(0));
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.NotExecutor.selector, fallbackWallet));
        vm.prank(fallbackWallet);
        vault.release(A, "invoice-1", carrierA, 1 * USDC);
    }

    function test_TheAdminCannotBeRenounced() public {
        vm.expectRevert(PolicyVault.AdminCannotBeRenounced.selector);
        vm.prank(admin);
        vault.renounceOwnership();
        assertEq(vault.owner(), admin);
    }

    function test_TheAdminMovesInTwoSteps() public {
        address next = makeAddr("nextAdmin");
        vm.prank(admin);
        vault.transferOwnership(next);
        assertEq(vault.owner(), admin);
        vm.prank(next);
        vault.acceptOwnership();
        assertEq(vault.owner(), next);
    }

    // ---- deposits and anchors ----

    function test_Deposit_CreditsOneCompany() public {
        _deposit(B, 25 * USDC);
        assertEq(vault.companyState(B).balance, 225 * USDC);
        assertEq(vault.companyState(A).balance, 5_000 * USDC);
        assertEq(usdc.balanceOf(address(vault)), 5_225 * USDC);
    }

    function test_Deposit_RevertsForACompanyNeverRegistered_AndOnZero() public {
        bytes32 nobody = bytes32(uint256(0xC));
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.UnknownCompany.selector, nobody));
        vm.prank(treasury);
        vault.deposit(nobody, 1 * USDC);

        vm.expectRevert(PolicyVault.ZeroAmount.selector);
        vm.prank(treasury);
        vault.deposit(A, 0);
    }

    function test_UsdcSentStraightToTheVaultBelongsToNoCompany() public {
        vm.prank(treasury);
        usdc.transfer(address(vault), 1_000 * USDC);
        assertEq(vault.companyState(A).balance, 5_000 * USDC);
        assertEq(vault.companyState(B).balance, 200 * USDC);
    }

    function test_Anchor_OnlyTheExecutor_AndCounts() public {
        vm.expectRevert(abi.encodeWithSelector(PolicyVault.NotExecutor.selector, admin));
        vm.prank(admin);
        vault.anchor("root-1");

        vm.expectEmit(address(vault));
        emit Anchored(1, "root-1");
        vm.prank(executor);
        vault.anchor("root-1");
        vm.prank(executor);
        vault.anchor("root-2");
        assertEq(vault.anchorCount(), 2);
        assertEq(vault.lastAnchor(), bytes32("root-2"));
    }

    // ---- whatever is done, the books never promise more than the vault holds ----

    function testFuzz_TheCompaniesBalancesAlwaysSumToWhatWasDepositedLessWhatWasPaid(uint256[24] calldata steps)
        public
    {
        uint256 strays = 0;
        for (uint256 i = 0; i < steps.length; ++i) {
            uint256 step = steps[i];
            bytes32 company = step % 2 == 0 ? A : B;
            address payee = company == A ? carrierA : carrierB;
            uint256 amount = bound(step >> 8, 1, 700 * USDC);
            uint256 kind = (step >> 4) % 4;
            if (kind == 0) {
                _deposit(company, amount);
            } else if (kind == 1) {
                vm.prank(executor);
                try vault.release(company, bytes32(i), payee, amount) {} catch {}
            } else if (kind == 2) {
                vm.prank(treasury);
                usdc.transfer(address(vault), amount);
                strays += amount;
            } else {
                vm.warp(vm.getBlockTimestamp() + 1 days);
            }
            uint256 books = vault.companyState(A).balance + vault.companyState(B).balance;
            assertEq(books + strays, usdc.balanceOf(address(vault)));
        }
    }

    // ---- helpers ----

    function _one(address payee) private pure returns (address[] memory payees) {
        payees = new address[](1);
        payees[0] = payee;
    }

    function _deposit(bytes32 company, uint256 amount) private {
        vm.prank(treasury);
        vault.deposit(company, amount);
    }

    function _release(bytes32 company, bytes32 paymentKey, address payee, uint256 amount) private {
        vm.prank(executor);
        vault.release(company, paymentKey, payee, amount);
    }

    function _setPayee(bytes32 company, uint256 key, address payee, bool allowed) private {
        uint256 nonce = vault.companyState(company).nonce;
        vault.setPayee(company, payee, allowed, nonce, _sign(key, _payeeHash(company, payee, allowed, nonce)));
    }

    function _setCaps(bytes32 company, uint256 key, uint256 perPayment, uint256 daily) private {
        uint256 nonce = vault.companyState(company).nonce;
        vault.setCaps(company, perPayment, daily, nonce, _sign(key, _capsHash(company, perPayment, daily, nonce)));
    }

    function _payeeHash(bytes32 company, address payee, bool allowed, uint256 nonce) private view returns (bytes32) {
        return keccak256(abi.encode(vault.SET_PAYEE_TYPEHASH(), company, payee, allowed, nonce));
    }

    function _capsHash(bytes32 company, uint256 perPayment, uint256 daily, uint256 nonce)
        private
        view
        returns (bytes32)
    {
        return keccak256(abi.encode(vault.SET_CAPS_TYPEHASH(), company, perPayment, daily, nonce));
    }

    function _ownerHash(bytes32 company, address newOwner, uint256 nonce) private view returns (bytes32) {
        return keccak256(abi.encode(vault.SET_COMPANY_OWNER_TYPEHASH(), company, newOwner, nonce));
    }

    /// @dev Signs typed data for `vault`, with the domain a wallet would build from `eip712Domain()`.
    function _sign(uint256 key, bytes32 structHash) private view returns (bytes memory) {
        (, string memory name, string memory version, uint256 chainId, address verifyingContract,,) =
            vault.eip712Domain();
        bytes32 domain = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes(name)),
                keccak256(bytes(version)),
                chainId,
                verifyingContract
            )
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, keccak256(abi.encodePacked("\x19\x01", domain, structHash)));
        return abi.encodePacked(r, s, v);
    }
}
